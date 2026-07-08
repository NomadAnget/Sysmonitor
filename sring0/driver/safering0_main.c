/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: safering0_main.c
 * Version: 1.0
 * Date: 2025-10-17
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - Driver Entry and Device Creation
 *   Implements DriverEntry, DriverUnload, and secure device creation using
 *   WdmlibIoCreateDeviceSecure. This is the primary fix for CVE-2020-14979.
 *
 * Security:
 *   - Device ACL via SDDL restricts access to SYSTEM and Administrators
 *   - Subsystem initialization order: ETW -> RateLimit -> MSR
 *   - Cleanup order reversed for safe shutdown
 */

#include <ntddk.h>
#include <wdmsec.h>

/*
 * initguid.h must be included BEFORE any header containing DEFINE_GUID
 * to properly instantiate the GUID. This prevents multiple definition
 * linker errors per Microsoft guidelines.
 * Reference:
 * https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/defining-and-exporting-new-guids
 */
#include <initguid.h>

#include "..\..\include\safering0_public.h"
#include "safering0_main.h"

#pragma comment(lib, "wdmsec.lib")

/*--------------------------------------------------------------------------*/
/* GUID Definition (must be after initguid.h)                               */
/*--------------------------------------------------------------------------*/

/*
 * Custom device class GUID for SafeRing0
 * {5A8B1C2D-3E4F-6A7B-8C9D-0E1F2A3B4C5D}
 */
DEFINE_GUID(GUID_SAFERING0_CLASS, 0x5a8b1c2d, 0x3e4f, 0x6a7b, 0x8c, 0x9d, 0x0e,
            0x1f, 0x2a, 0x3b, 0x4c, 0x5d);

/*--------------------------------------------------------------------------*/
/* Global State                                                             */
/*--------------------------------------------------------------------------*/

PSR0_CONTEXT g_Sr0Context = NULL;

/*--------------------------------------------------------------------------*/
/* SDDL Security Descriptor                                                 */
/*--------------------------------------------------------------------------*/

/*
 * THE fix for CVE-2020-14979:
 * Only SYSTEM (SY) and Administrators (BA) get Generic All access.
 * All other processes are denied at device open time.
 */
static UNICODE_STRING g_SecureSDDL = RTL_CONSTANT_STRING(SR0_SDDL_STRING);

/*--------------------------------------------------------------------------*/
/* Forward Declarations (Static Helpers)                                    */
/*--------------------------------------------------------------------------*/

_IRQL_requires_(PASSIVE_LEVEL) static NTSTATUS
    Sr0CreateDevice(_In_ PDRIVER_OBJECT DriverObject);

_IRQL_requires_(PASSIVE_LEVEL) static NTSTATUS Sr0AllocateContext(VOID);

_IRQL_requires_(PASSIVE_LEVEL) static VOID Sr0FreeContext(VOID);

_IRQL_requires_(PASSIVE_LEVEL) static NTSTATUS Sr0InitializeSubsystems(VOID);

_IRQL_requires_(PASSIVE_LEVEL) static VOID Sr0ShutdownSubsystems(VOID);

/*--------------------------------------------------------------------------*/
/* Driver Entry Point                                                       */
/*--------------------------------------------------------------------------*/

/**
 * @function   DriverEntry
 * @purpose    Driver initialization entry point
 * @precondition IRQL == PASSIVE_LEVEL
 * @returns    STATUS_SUCCESS on success, error code otherwise
 */
_Use_decl_annotations_ NTSTATUS DriverEntry(_In_ PDRIVER_OBJECT DriverObject,
                                            _In_ PUNICODE_STRING RegistryPath) {
  NTSTATUS status;

  UNREFERENCED_PARAMETER(RegistryPath);

  /* Allocate global context */
  status = Sr0AllocateContext();
  if (!NT_SUCCESS(status)) {
    return status;
  }

  /* Create secure device (CVE-2020-14979 fix) */
  status = Sr0CreateDevice(DriverObject);
  if (!NT_SUCCESS(status)) {
    Sr0FreeContext();
    return status;
  }

  /* Set up dispatch routines */
  DriverObject->MajorFunction[IRP_MJ_CREATE] = Sr0DispatchCreate;
  DriverObject->MajorFunction[IRP_MJ_CLOSE] = Sr0DispatchClose;
  DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] = Sr0DispatchDeviceControl;
  DriverObject->DriverUnload = Sr0DriverUnload;

  /* Initialize subsystems */
  status = Sr0InitializeSubsystems();
  if (!NT_SUCCESS(status)) {
    IoDeleteSymbolicLink(&g_Sr0Context->SymLinkName);
    IoDeleteDevice(g_Sr0Context->DeviceObject);
    Sr0FreeContext();
    return status;
  }

  /* Read MSR opt-in setting */
  g_Sr0Context->MsrWriteOptIn = Sr0ReadMsrOptInFromRegistry();
  g_Sr0Context->Enabled = TRUE;

  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* Driver Unload                                                            */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0DriverUnload
 * @purpose    Clean up driver resources with safe synchronization
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition All resources freed, no in-flight operations
 * @thread-safety Waits for in-flight IOCTLs via IoCompletedEvent
 * @side-effects Blocks up to 10 seconds waiting for pending operations
 */
_Use_decl_annotations_ VOID Sr0DriverUnload(_In_ PDRIVER_OBJECT DriverObject) {
  LARGE_INTEGER timeout;

  UNREFERENCED_PARAMETER(DriverObject);

  if (g_Sr0Context == NULL) {
    return;
  }

  /*
   * Signal shutdown - new operations will fail fast.
   * Use InterlockedExchange for visibility across all CPUs.
   */
  InterlockedExchange(&g_Sr0Context->Enabled, FALSE);

  /*
   * Wait for in-flight IOCTLs to complete.
   * IoCompletedEvent is signaled when ActiveIoCount reaches 0.
   * Timeout of 10 seconds prevents indefinite hang during shutdown.
   */
  timeout.QuadPart = -10 * 10000000LL; /* 10 seconds in 100ns units */
  KeWaitForSingleObject(&g_Sr0Context->IoCompletedEvent, Executive, KernelMode,
                        FALSE, &timeout);

  /* Shutdown subsystems in reverse order */
  Sr0ShutdownSubsystems();

  /* Delete symbolic link and device */
  IoDeleteSymbolicLink(&g_Sr0Context->SymLinkName);
  IoDeleteDevice(g_Sr0Context->DeviceObject);

  /* Free context */
  Sr0FreeContext();
}

/*--------------------------------------------------------------------------*/
/* Secure Device Creation                                                   */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0CreateDevice
 * @purpose    Create device with secure SDDL (CVE-2020-14979 fix)
 * @precondition IRQL == PASSIVE_LEVEL
 * @returns    STATUS_SUCCESS on success
 * @thread-safety Single-threaded init only
 * @side-effects Creates device object and symbolic link
 */
_Use_decl_annotations_ static NTSTATUS Sr0CreateDevice(
    _In_ PDRIVER_OBJECT DriverObject) {
  NTSTATUS status;

  /* Initialize device name */
  RtlInitUnicodeString(&g_Sr0Context->DeviceName, SR0_DEVICE_NAME_U);
  RtlInitUnicodeString(&g_Sr0Context->SymLinkName, SR0_SYMLINK_NAME_U);

  /* Create device with security descriptor - THE FIX for CVE-2020-14979 */
  status = WdmlibIoCreateDeviceSecure(
      DriverObject, 0, /* No device extension needed */
      &g_Sr0Context->DeviceName, FILE_DEVICE_UNKNOWN, FILE_DEVICE_SECURE_OPEN,
      FALSE,         /* Not exclusive */
      &g_SecureSDDL, /* Admin-only access */
      &GUID_SAFERING0_CLASS, &g_Sr0Context->DeviceObject);

  if (!NT_SUCCESS(status)) {
    g_Sr0Context->DeviceObject = NULL;
    return status;
  }

  /* Defensive null check - should never happen if status is success */
  if (g_Sr0Context->DeviceObject == NULL) {
    return STATUS_UNSUCCESSFUL;
  }

  /* Create symbolic link for user-mode access */
  status = IoCreateSymbolicLink(&g_Sr0Context->SymLinkName,
                                &g_Sr0Context->DeviceName);

  if (!NT_SUCCESS(status)) {
    IoDeleteDevice(g_Sr0Context->DeviceObject);
    g_Sr0Context->DeviceObject = NULL;
    return status;
  }

  /* Enable buffered I/O */
  g_Sr0Context->DeviceObject->Flags |= DO_BUFFERED_IO;
  g_Sr0Context->DeviceObject->Flags &= ~DO_DEVICE_INITIALIZING;

  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* Context Management                                                       */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0AllocateContext
 * @purpose    Allocate and initialize global context
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition g_Sr0Context points to valid context or NULL
 * @returns    STATUS_SUCCESS or STATUS_INSUFFICIENT_RESOURCES
 * @thread-safety Single-threaded init only
 * @side-effects Allocates non-paged pool memory
 */
_Use_decl_annotations_ static NTSTATUS Sr0AllocateContext(VOID) {
  g_Sr0Context = (PSR0_CONTEXT)ExAllocatePool2(
      POOL_FLAG_NON_PAGED, sizeof(SR0_CONTEXT), SR0_POOL_TAG_GENERAL);

  if (g_Sr0Context == NULL) {
    return STATUS_INSUFFICIENT_RESOURCES;
  }

  RtlZeroMemory(g_Sr0Context, sizeof(SR0_CONTEXT));

  /*
   * Initialize I/O completion event as signaled (notification event).
   * Event is cleared when ActiveIoCount > 0, set when it reaches 0.
   * This enables safe unload synchronization - DriverUnload waits on this.
   */
  KeInitializeEvent(&g_Sr0Context->IoCompletedEvent, NotificationEvent, TRUE);

  /* Set default capabilities */
  g_Sr0Context->Capabilities = SR0_CAP_MSR_READ | SR0_CAP_IO_PORT |
                               SR0_CAP_PHYS_MEMORY | SR0_CAP_PCI_CONFIG |
                               SR0_CAP_RATE_LIMIT | SR0_CAP_ETW_TELEMETRY;

  return STATUS_SUCCESS;
}

/**
 * @function   Sr0FreeContext
 * @purpose    Free global context
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition g_Sr0Context == NULL
 * @thread-safety Single-threaded shutdown only
 * @side-effects Frees non-paged pool memory
 */
_Use_decl_annotations_ static VOID Sr0FreeContext(VOID) {
  if (g_Sr0Context != NULL) {
    ExFreePoolWithTag(g_Sr0Context, SR0_POOL_TAG_GENERAL);
    g_Sr0Context = NULL;
  }
}

/*--------------------------------------------------------------------------*/
/* Subsystem Initialization                                                 */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0InitializeSubsystems
 * @purpose    Initialize all subsystems in order
 * @precondition IRQL == PASSIVE_LEVEL, device created
 * @postcondition All subsystems initialized or none (atomic)
 * @returns    STATUS_SUCCESS or first subsystem failure code
 * @thread-safety Single-threaded init only
 * @side-effects Initializes ETW, rate limiting, MSR policy
 */
_Use_decl_annotations_ static NTSTATUS Sr0InitializeSubsystems(VOID) {
  NTSTATUS status;

  /* 1. ETW first - needed for logging other init */
  status = Sr0EtwInitialize();
  if (!NT_SUCCESS(status)) {
    return status;
  }

  /* 2. Rate limiting */
  status = Sr0RateLimitInitialize();
  if (!NT_SUCCESS(status)) {
    Sr0EtwShutdown();
    return status;
  }

  /* 3. MSR policy */
  status = Sr0MsrInitialize();
  if (!NT_SUCCESS(status)) {
    Sr0RateLimitShutdown();
    Sr0EtwShutdown();
    return status;
  }

  return STATUS_SUCCESS;
}

/**
 * @function   Sr0ShutdownSubsystems
 * @purpose    Shutdown subsystems in reverse order
 * @precondition IRQL == PASSIVE_LEVEL, driver unloading
 * @postcondition All subsystems shut down
 * @thread-safety Single-threaded shutdown only
 * @side-effects Shuts down MSR, rate limiting, ETW (reverse init order)
 */
_Use_decl_annotations_ static VOID Sr0ShutdownSubsystems(VOID) {
  Sr0MsrShutdown();
  Sr0RateLimitShutdown();
  Sr0EtwShutdown();
}

/*--------------------------------------------------------------------------*/
/* IRP Dispatch: Create                                                     */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0DispatchCreate
 * @purpose    Handle IRP_MJ_CREATE (device open)
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition Handle reference count incremented if enabled
 * @returns    STATUS_SUCCESS if enabled, STATUS_DEVICE_NOT_READY if disabled
 * @thread-safety Thread-safe via interlocked operations
 * @side-effects Increments g_Sr0Context->RefCount on success
 */
_Use_decl_annotations_ NTSTATUS
Sr0DispatchCreate(_In_ PDEVICE_OBJECT DeviceObject, _Inout_ PIRP Irp) {
  NTSTATUS status;

  UNREFERENCED_PARAMETER(DeviceObject);

  /*
   * Check if driver is enabled before allowing new handles.
   * This prevents new opens during shutdown (after Enabled = FALSE).
   */
  if (!InterlockedCompareExchange(&g_Sr0Context->Enabled, TRUE, TRUE)) {
    status = STATUS_DEVICE_NOT_READY;
    Irp->IoStatus.Status = status;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return status;
  }

  /* Increment reference count */
  InterlockedIncrement(&g_Sr0Context->RefCount);

  Irp->IoStatus.Status = STATUS_SUCCESS;
  Irp->IoStatus.Information = 0;
  IoCompleteRequest(Irp, IO_NO_INCREMENT);

  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* IRP Dispatch: Close                                                      */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0DispatchClose
 * @purpose    Handle IRP_MJ_CLOSE (device close)
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition Handle reference count decremented
 * @returns    STATUS_SUCCESS always
 * @thread-safety Thread-safe via interlocked decrement
 * @side-effects Decrements g_Sr0Context->RefCount
 */
_Use_decl_annotations_ NTSTATUS
Sr0DispatchClose(_In_ PDEVICE_OBJECT DeviceObject, _Inout_ PIRP Irp) {
  UNREFERENCED_PARAMETER(DeviceObject);

  /* Decrement reference count */
  InterlockedDecrement(&g_Sr0Context->RefCount);

  Irp->IoStatus.Status = STATUS_SUCCESS;
  Irp->IoStatus.Information = 0;
  IoCompleteRequest(Irp, IO_NO_INCREMENT);

  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* Registry Helper                                                          */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0ReadMsrOptInFromRegistry
 * @purpose    Read AllowMsrWrites DWORD value from registry
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition None (read-only operation)
 * @returns    TRUE if registry value exists and is non-zero, FALSE otherwise
 * @thread-safety Thread-safe (read-only registry access)
 * @side-effects None
 *
 * Full registry path (after concatenation):
 *   \Registry\Machine\SYSTEM\CurrentControlSet\Services\SafeRing0\Parameters
 *
 * This corresponds to user-visible path:
 *   HKLM\SYSTEM\CurrentControlSet\Services\SafeRing0\Parameters
 *
 * Value name: AllowMsrWrites (REG_DWORD)
 *   0 = MSR writes denied (default, most secure)
 *   1 = MSR writes allowed for whitelisted registers only
 */
_Use_decl_annotations_ BOOLEAN Sr0ReadMsrOptInFromRegistry(VOID) {
  NTSTATUS status;
  HANDLE keyHandle = NULL;
  UNICODE_STRING keyPath;
  UNICODE_STRING valueName;
  OBJECT_ATTRIBUTES objAttr;
  UCHAR buffer[sizeof(KEY_VALUE_PARTIAL_INFORMATION) + sizeof(ULONG)];
  PKEY_VALUE_PARTIAL_INFORMATION valueInfo;
  ULONG resultLength;
  BOOLEAN result = FALSE;

  /*
   * Build full registry path via string concatenation:
   * L"\\Registry\\Machine\\" + SR0_MSR_OPTIN_REGKEY (from safering0_public.h)
   * SR0_MSR_OPTIN_REGKEY =
   * L"SYSTEM\\CurrentControlSet\\Services\\SafeRing0\\Parameters"
   */
  RtlInitUnicodeString(&keyPath, L"\\Registry\\Machine\\" SR0_MSR_OPTIN_REGKEY);
  RtlInitUnicodeString(&valueName, SR0_MSR_OPTIN_VALUE);

  InitializeObjectAttributes(
      &objAttr, &keyPath, OBJ_CASE_INSENSITIVE | OBJ_KERNEL_HANDLE, NULL, NULL);

  status = ZwOpenKey(&keyHandle, KEY_READ, &objAttr);
  if (!NT_SUCCESS(status)) {
    return FALSE; /* Key doesn't exist = opt-in disabled */
  }

  valueInfo = (PKEY_VALUE_PARTIAL_INFORMATION)buffer;
  status = ZwQueryValueKey(keyHandle, &valueName, KeyValuePartialInformation,
                           valueInfo, sizeof(buffer), &resultLength);

  if (NT_SUCCESS(status) && valueInfo->Type == REG_DWORD) {
    ULONG value = *(PULONG)valueInfo->Data;
    result = (value != 0);
  }

  ZwClose(keyHandle);
  return result;
}
