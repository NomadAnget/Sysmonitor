/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: safering0_main.h
 * Version: 1.0
 * Date: 2025-12-04
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - Driver Internal Structures
 *   Defines driver context, internal state, and forward declarations for
 *   subsystem initialization. This header is kernel-mode only.
 *
 * Security:
 *   - CVE-2020-14979 fix via WdmlibIoCreateDeviceSecure
 *   - Rate limiting state for DoS prevention
 *   - ETW provider handle for forensic logging
 */

#pragma once

#ifndef _KERNEL_MODE
#error "This header is for kernel-mode only."
#endif

#include <ntddk.h>
#include <wdmsec.h>

#ifdef __cplusplus
extern "C" {
#endif

/*--------------------------------------------------------------------------*/
/* Driver Context                                                           */
/*--------------------------------------------------------------------------*/

/*
 * SR0_CONTEXT - Global driver state
 *
 * Single instance allocated in DriverEntry, freed in DriverUnload.
 * All fields protected by appropriate synchronization.
 *
 * Threading model:
 * - RefCount: Interlocked ops (open handle tracking)
 * - ActiveIoCount: Interlocked ops (in-flight IOCTL tracking)
 * - Enabled: Interlocked read/write (driver state flag)
 * - IoCompletedEvent: Signaled when ActiveIoCount reaches 0
 */
typedef struct _SR0_CONTEXT {
  PDEVICE_OBJECT DeviceObject; /* Our device object */
  UNICODE_STRING DeviceName;   /* \Device\WinRing0_1_2_0 */
  UNICODE_STRING SymLinkName;  /* \DosDevices\WinRing0_1_2_0 */

  volatile LONG RefCount;      /* Open handle count */
  volatile LONG Enabled;       /* Driver operational flag */
  volatile LONG ActiveIoCount; /* In-flight IOCTL count for safe unload */
  KEVENT IoCompletedEvent;     /* Signaled when ActiveIoCount == 0 */

  ULONG Capabilities;  /* Active capability flags */
  ULONG MsrWriteOptIn; /* Cached registry opt-in value (0 or 1) */

} SR0_CONTEXT, *PSR0_CONTEXT;

/*
 * Global driver context - single instance
 */
extern PSR0_CONTEXT g_Sr0Context;

/*--------------------------------------------------------------------------*/
/* Device Class GUID                                                        */
/*--------------------------------------------------------------------------*/

/*
 * Custom device class GUID for SafeRing0
 * {5A8B1C2D-3E4F-6A7B-8C9D-0E1F2A3B4C5D}
 *
 * Note: DEFINE_GUID is in safering0_main.c with initguid.h to avoid
 * multiple definition linker errors per Microsoft guidelines.
 */
EXTERN_C const GUID GUID_SAFERING0_CLASS;

/*--------------------------------------------------------------------------*/
/* Driver Entry Points                                                      */
/*--------------------------------------------------------------------------*/

DRIVER_INITIALIZE DriverEntry;
DRIVER_UNLOAD Sr0DriverUnload;

/*--------------------------------------------------------------------------*/
/* IRP Dispatch Routines                                                    */
/*--------------------------------------------------------------------------*/

_IRQL_requires_max_(PASSIVE_LEVEL)
    _Dispatch_type_(IRP_MJ_CREATE) DRIVER_DISPATCH Sr0DispatchCreate;

_IRQL_requires_max_(PASSIVE_LEVEL)
    _Dispatch_type_(IRP_MJ_CLOSE) DRIVER_DISPATCH Sr0DispatchClose;

_IRQL_requires_max_(PASSIVE_LEVEL) _Dispatch_type_(IRP_MJ_DEVICE_CONTROL)
    DRIVER_DISPATCH Sr0DispatchDeviceControl;

/*--------------------------------------------------------------------------*/
/* Subsystem Initialization (Forward Declarations)                          */
/*--------------------------------------------------------------------------*/

/*
 * Rate Limiting - ratelimit.c
 */
_IRQL_requires_(PASSIVE_LEVEL) NTSTATUS Sr0RateLimitInitialize(VOID);

_IRQL_requires_(PASSIVE_LEVEL) VOID Sr0RateLimitShutdown(VOID);

/*
 * ETW Telemetry - telemetry.c
 */
_IRQL_requires_(PASSIVE_LEVEL) NTSTATUS Sr0EtwInitialize(VOID);

_IRQL_requires_(PASSIVE_LEVEL) VOID Sr0EtwShutdown(VOID);

/*
 * ETW Logging Functions
 * All logging functions are safe at any IRQL (TraceLogging is lock-free).
 * PCI offset widened to USHORT for PCIe extended config space (0-4095).
 */
_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogMsrRead(_In_ ULONG ProcessId, _In_ ULONG MsrIndex,
                     _In_ ULONG64 Value);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogMsrWrite(_In_ ULONG ProcessId, _In_ ULONG MsrIndex,
                      _In_ ULONG64 Value);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogMsrBlocked(_In_ ULONG ProcessId, _In_ ULONG MsrIndex,
                        _In_ ULONG64 Value, _In_ PCSTR Reason);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogMemoryRead(_In_ ULONG ProcessId, _In_ ULONG64 PhysicalAddress,
                        _In_ ULONG Size);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogMemoryWrite(_In_ ULONG ProcessId, _In_ ULONG64 PhysicalAddress,
                         _In_ ULONG Size);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogMemoryBlocked(_In_ ULONG ProcessId, _In_ ULONG64 PhysicalAddress,
                           _In_ PCSTR Reason);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogIoPortRead(_In_ ULONG ProcessId, _In_ USHORT PortNumber,
                        _In_ ULONG Value, _In_ UCHAR Size);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogIoPortWrite(_In_ ULONG ProcessId, _In_ USHORT PortNumber,
                         _In_ ULONG Value, _In_ UCHAR Size);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogPciRead(_In_ ULONG ProcessId, _In_ UCHAR Bus, _In_ UCHAR Device,
                     _In_ UCHAR Function, _In_ USHORT Offset);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogPciWrite(_In_ ULONG ProcessId, _In_ UCHAR Bus, _In_ UCHAR Device,
                      _In_ UCHAR Function, _In_ USHORT Offset);

_IRQL_requires_max_(HIGH_LEVEL) VOID
    Sr0EtwLogRateExceeded(_In_ ULONG ProcessId, _In_ BOOLEAN IsGlobal);

/*
 * MSR Policy - msr.c
 */
_IRQL_requires_(PASSIVE_LEVEL) NTSTATUS Sr0MsrInitialize(VOID);

_IRQL_requires_(PASSIVE_LEVEL) VOID Sr0MsrShutdown(VOID);

/*--------------------------------------------------------------------------*/
/* I/O Tracking Helpers (for safe unload synchronization)                   */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0IoEnter
 * @purpose    Track entry into an I/O operation (call at IOCTL start)
 * @precondition g_Sr0Context != NULL
 * @postcondition ActiveIoCount incremented, IoCompletedEvent cleared if count >
 * 0
 * @thread-safety Thread-safe via interlocked operations
 *
 * Call this at the start of Sr0DispatchDeviceControl to track in-flight IOCTLs.
 * The corresponding Sr0IoLeave must be called before completing the IRP.
 */
__inline VOID Sr0IoEnter(VOID) {
  LONG count = InterlockedIncrement(&g_Sr0Context->ActiveIoCount);
  if (count == 1) {
    /* First active I/O - clear the completion event */
    KeClearEvent(&g_Sr0Context->IoCompletedEvent);
  }
}

/**
 * @function   Sr0IoLeave
 * @purpose    Track exit from an I/O operation (call before IRP completion)
 * @precondition g_Sr0Context != NULL, Sr0IoEnter was called
 * @postcondition ActiveIoCount decremented, IoCompletedEvent set if count == 0
 * @thread-safety Thread-safe via interlocked operations
 *
 * Call this before IoCompleteRequest in Sr0DispatchDeviceControl.
 * When count reaches 0, signals IoCompletedEvent to unblock DriverUnload.
 */
__inline VOID Sr0IoLeave(VOID) {
  LONG count = InterlockedDecrement(&g_Sr0Context->ActiveIoCount);
  if (count == 0) {
    /* Last active I/O completed - signal the completion event */
    KeSetEvent(&g_Sr0Context->IoCompletedEvent, IO_NO_INCREMENT, FALSE);
  }
}

/*--------------------------------------------------------------------------*/
/* Utility Functions                                                        */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0ReadMsrOptInFromRegistry
 * @purpose    Read AllowMsrWrites registry value
 * @precondition IRQL == PASSIVE_LEVEL
 * @returns    TRUE if opt-in enabled, FALSE otherwise
 */
_IRQL_requires_(PASSIVE_LEVEL) BOOLEAN Sr0ReadMsrOptInFromRegistry(VOID);

#ifdef __cplusplus
}
#endif
