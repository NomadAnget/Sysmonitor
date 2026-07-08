/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: dispatch.c
 * Version: 1.0
 * Date: 2025-10-03
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - IOCTL Dispatch Implementation
 *   Handles all WinRing0-compatible IOCTLs with security validation.
 *   Implements MSR, I/O port, physical memory, and PCI config access.
 *
 * Security:
 *   - Input buffer validation before any operation
 *   - Kernel address blocking for physical memory access
 *   - Rate limiting on all operations
 *   - ETW logging for forensics
 *   - HALT IOCTL always blocked (no legitimate use case)
 */

#include <intrin.h>
#include <ntddk.h>

#include "..\..\include\safering0_public.h"
#include "..\..\include\safering0_version.h"
#include "msr.h"
#include "ratelimit.h"
#include "safering0_main.h"

/*--------------------------------------------------------------------------*/
/* PCI Configuration Space I/O Ports                                        */
/*--------------------------------------------------------------------------*/

#define PCI_CONFIG_ADDRESS_PORT 0xCF8
#define PCI_CONFIG_DATA_PORT 0xCFC

/*--------------------------------------------------------------------------*/
/* Forward Declarations                                                     */
/*--------------------------------------------------------------------------*/

static NTSTATUS Sr0HandleGetDriverVersion(_In_ PIRP Irp,
                                          _In_ PIO_STACK_LOCATION IrpSp);
static NTSTATUS Sr0HandleGetRefCount(_In_ PIRP Irp,
                                     _In_ PIO_STACK_LOCATION IrpSp);
static NTSTATUS Sr0HandleReadMsr(_In_ PIRP Irp, _In_ PIO_STACK_LOCATION IrpSp,
                                 _In_ ULONG ProcessId);
static NTSTATUS Sr0HandleWriteMsr(_In_ PIRP Irp, _In_ PIO_STACK_LOCATION IrpSp,
                                  _In_ ULONG ProcessId);
static NTSTATUS Sr0HandleReadIoPort(_In_ PIRP Irp,
                                    _In_ PIO_STACK_LOCATION IrpSp,
                                    _In_ ULONG ProcessId, _In_ UCHAR Size);
static NTSTATUS Sr0HandleWriteIoPort(_In_ PIRP Irp,
                                     _In_ PIO_STACK_LOCATION IrpSp,
                                     _In_ ULONG ProcessId, _In_ UCHAR Size);
static NTSTATUS Sr0HandleReadMemory(_In_ PIRP Irp,
                                    _In_ PIO_STACK_LOCATION IrpSp,
                                    _In_ ULONG ProcessId);
static NTSTATUS Sr0HandleWriteMemory(_In_ PIRP Irp,
                                     _In_ PIO_STACK_LOCATION IrpSp,
                                     _In_ ULONG ProcessId);
static NTSTATUS Sr0HandleReadPciConfig(_In_ PIRP Irp,
                                       _In_ PIO_STACK_LOCATION IrpSp,
                                       _In_ ULONG ProcessId);
static NTSTATUS Sr0HandleWritePciConfig(_In_ PIRP Irp,
                                        _In_ PIO_STACK_LOCATION IrpSp,
                                        _In_ ULONG ProcessId);

/*--------------------------------------------------------------------------*/
/* Main Dispatch Function                                                   */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0DispatchDeviceControl
 * @purpose    Handle IRP_MJ_DEVICE_CONTROL (IOCTLs)
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition IRP completed with appropriate status
 * @returns    IOCTL result status
 * @thread-safety Thread-safe via I/O tracking
 */
_Use_decl_annotations_ NTSTATUS
Sr0DispatchDeviceControl(_In_ PDEVICE_OBJECT DeviceObject, _Inout_ PIRP Irp) {
  NTSTATUS status = STATUS_INVALID_DEVICE_REQUEST;
  PIO_STACK_LOCATION irpSp;
  ULONG ioControlCode;
  ULONG processId;

  UNREFERENCED_PARAMETER(DeviceObject);

  /* Track I/O for safe unload */
  Sr0IoEnter();

  /* Check if driver is enabled */
  if (!InterlockedCompareExchange(&g_Sr0Context->Enabled, TRUE, TRUE)) {
    status = STATUS_DEVICE_NOT_READY;
    goto Exit;
  }

  irpSp = IoGetCurrentIrpStackLocation(Irp);
  ioControlCode = irpSp->Parameters.DeviceIoControl.IoControlCode;
  processId = (ULONG)(ULONG_PTR)PsGetCurrentProcessId();

  switch (ioControlCode) {
    /* Driver Info */
    case IOCTL_OLS_GET_DRIVER_VERSION:
      status = Sr0HandleGetDriverVersion(Irp, irpSp);
      break;
    case IOCTL_OLS_GET_REFCOUNT:
      status = Sr0HandleGetRefCount(Irp, irpSp);
      break;

    /* MSR Operations */
    case IOCTL_OLS_READ_MSR:
    case IOCTL_OLS_READ_PMC:
      status = Sr0HandleReadMsr(Irp, irpSp, processId);
      break;
    case IOCTL_OLS_WRITE_MSR:
      status = Sr0HandleWriteMsr(Irp, irpSp, processId);
      break;

    /* HALT - Always blocked */
    case IOCTL_OLS_HALT:
      status = STATUS_ACCESS_DENIED;
      break;

    /* I/O Port Operations */
    case IOCTL_OLS_READ_IO_PORT_BYTE:
      status = Sr0HandleReadIoPort(Irp, irpSp, processId, 1);
      break;
    case IOCTL_OLS_READ_IO_PORT_WORD:
      status = Sr0HandleReadIoPort(Irp, irpSp, processId, 2);
      break;
    case IOCTL_OLS_READ_IO_PORT_DWORD:
      status = Sr0HandleReadIoPort(Irp, irpSp, processId, 4);
      break;
    case IOCTL_OLS_WRITE_IO_PORT_BYTE:
      status = Sr0HandleWriteIoPort(Irp, irpSp, processId, 1);
      break;
    case IOCTL_OLS_WRITE_IO_PORT_WORD:
      status = Sr0HandleWriteIoPort(Irp, irpSp, processId, 2);
      break;
    case IOCTL_OLS_WRITE_IO_PORT_DWORD:
      status = Sr0HandleWriteIoPort(Irp, irpSp, processId, 4);
      break;

    /* Physical Memory Operations */
    case IOCTL_OLS_READ_MEMORY:
      status = Sr0HandleReadMemory(Irp, irpSp, processId);
      break;
    case IOCTL_OLS_WRITE_MEMORY:
      status = Sr0HandleWriteMemory(Irp, irpSp, processId);
      break;

    /* PCI Configuration Space */
    case IOCTL_OLS_READ_PCI_CONFIG:
      status = Sr0HandleReadPciConfig(Irp, irpSp, processId);
      break;
    case IOCTL_OLS_WRITE_PCI_CONFIG:
      status = Sr0HandleWritePciConfig(Irp, irpSp, processId);
      break;

    default:
      status = STATUS_INVALID_DEVICE_REQUEST;
      break;
  }

Exit:
  Irp->IoStatus.Status = status;
  Sr0IoLeave();
  IoCompleteRequest(Irp, IO_NO_INCREMENT);
  return status;
}

/*--------------------------------------------------------------------------*/
/* Driver Info Handlers                                                     */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0HandleGetDriverVersion
 * @purpose    Return WinRing0-compatible driver version
 * @returns    STATUS_SUCCESS with version in output buffer
 */
static NTSTATUS Sr0HandleGetDriverVersion(_In_ PIRP Irp,
                                          _In_ PIO_STACK_LOCATION IrpSp) {
  PULONG outputBuffer;

  if (IrpSp->Parameters.DeviceIoControl.OutputBufferLength < sizeof(ULONG)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  outputBuffer = (PULONG)Irp->AssociatedIrp.SystemBuffer;
  *outputBuffer = SR0_COMPAT_VERSION_PACKED;
  Irp->IoStatus.Information = sizeof(ULONG);
  return STATUS_SUCCESS;
}

/**
 * @function   Sr0HandleGetRefCount
 * @purpose    Return current handle reference count
 * @returns    STATUS_SUCCESS with ref count in output buffer
 */
static NTSTATUS Sr0HandleGetRefCount(_In_ PIRP Irp,
                                     _In_ PIO_STACK_LOCATION IrpSp) {
  PULONG outputBuffer;

  if (IrpSp->Parameters.DeviceIoControl.OutputBufferLength < sizeof(ULONG)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  outputBuffer = (PULONG)Irp->AssociatedIrp.SystemBuffer;
  *outputBuffer = (ULONG)g_Sr0Context->RefCount;
  Irp->IoStatus.Information = sizeof(ULONG);
  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* MSR Handlers                                                             */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0HandleReadMsr
 * @purpose    Read MSR register (also handles PMC read)
 * @returns    STATUS_SUCCESS with 64-bit value, or error
 */
static NTSTATUS Sr0HandleReadMsr(_In_ PIRP Irp, _In_ PIO_STACK_LOCATION IrpSp,
                                 _In_ ULONG ProcessId) {
  POLS_READ_MSR_INPUT input;
  PULONG64 outputBuffer;

  if (IrpSp->Parameters.DeviceIoControl.InputBufferLength <
      sizeof(OLS_READ_MSR_INPUT)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  if (IrpSp->Parameters.DeviceIoControl.OutputBufferLength < sizeof(ULONG64)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  input = (POLS_READ_MSR_INPUT)Irp->AssociatedIrp.SystemBuffer;
  outputBuffer = (PULONG64)Irp->AssociatedIrp.SystemBuffer;

  NTSTATUS status = Sr0MsrRead(input->Register, outputBuffer, ProcessId);
  Irp->IoStatus.Information = NT_SUCCESS(status) ? sizeof(ULONG64) : 0;
  return status;
}

/**
 * @function   Sr0HandleWriteMsr
 * @purpose    Write MSR register with policy check
 * @returns    STATUS_SUCCESS, STATUS_ACCESS_DENIED, or error
 */
static NTSTATUS Sr0HandleWriteMsr(_In_ PIRP Irp, _In_ PIO_STACK_LOCATION IrpSp,
                                  _In_ ULONG ProcessId) {
  POLS_WRITE_MSR_INPUT input;

  if (IrpSp->Parameters.DeviceIoControl.InputBufferLength <
      sizeof(OLS_WRITE_MSR_INPUT)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  input = (POLS_WRITE_MSR_INPUT)Irp->AssociatedIrp.SystemBuffer;
  Irp->IoStatus.Information = 0;
  return Sr0MsrWrite(input->Register, input->Value.QuadPart, ProcessId);
}

/*--------------------------------------------------------------------------*/
/* I/O Port Handlers                                                        */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0HandleReadIoPort
 * @purpose    Read from I/O port (byte/word/dword)
 * @returns    STATUS_SUCCESS with value in output buffer
 */
static NTSTATUS Sr0HandleReadIoPort(_In_ PIRP Irp,
                                    _In_ PIO_STACK_LOCATION IrpSp,
                                    _In_ ULONG ProcessId, _In_ UCHAR Size) {
  POLS_READ_IO_PORT_INPUT input;
  PULONG outputBuffer;
  SR0_RATE_CHECK_RESULT rateResult;
  ULONG value;
  USHORT port;

  if (IrpSp->Parameters.DeviceIoControl.InputBufferLength <
      sizeof(OLS_READ_IO_PORT_INPUT)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  if (IrpSp->Parameters.DeviceIoControl.OutputBufferLength < sizeof(ULONG)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  /* Rate limit check */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    Irp->IoStatus.Information = 0;
    return STATUS_QUOTA_EXCEEDED;
  }

  input = (POLS_READ_IO_PORT_INPUT)Irp->AssociatedIrp.SystemBuffer;
  outputBuffer = (PULONG)Irp->AssociatedIrp.SystemBuffer;
  port = (USHORT)input->PortNumber;

  switch (Size) {
    case 1:
      value = __inbyte(port);
      break;
    case 2:
      value = __inword(port);
      break;
    case 4:
      value = __indword(port);
      break;
    default:
      Irp->IoStatus.Information = 0;
      return STATUS_INVALID_PARAMETER;
  }

  Sr0EtwLogIoPortRead(ProcessId, port, value, Size);
  *outputBuffer = value;
  Irp->IoStatus.Information = sizeof(ULONG);
  return STATUS_SUCCESS;
}

/**
 * @function   Sr0HandleWriteIoPort
 * @purpose    Write to I/O port (byte/word/dword)
 * @returns    STATUS_SUCCESS on success
 */
static NTSTATUS Sr0HandleWriteIoPort(_In_ PIRP Irp,
                                     _In_ PIO_STACK_LOCATION IrpSp,
                                     _In_ ULONG ProcessId, _In_ UCHAR Size) {
  POLS_WRITE_IO_PORT_INPUT input;
  SR0_RATE_CHECK_RESULT rateResult;
  USHORT port;
  ULONG value;

  if (IrpSp->Parameters.DeviceIoControl.InputBufferLength <
      sizeof(OLS_WRITE_IO_PORT_INPUT)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  /* Rate limit check */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    Irp->IoStatus.Information = 0;
    return STATUS_QUOTA_EXCEEDED;
  }

  input = (POLS_WRITE_IO_PORT_INPUT)Irp->AssociatedIrp.SystemBuffer;
  port = (USHORT)input->PortNumber;
  value = input->u.LongData;

  switch (Size) {
    case 1:
      __outbyte(port, (UCHAR)value);
      break;
    case 2:
      __outword(port, (USHORT)value);
      break;
    case 4:
      __outdword(port, value);
      break;
    default:
      Irp->IoStatus.Information = 0;
      return STATUS_INVALID_PARAMETER;
  }

  Sr0EtwLogIoPortWrite(ProcessId, port, value, Size);
  Irp->IoStatus.Information = 0;
  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* Physical Memory Handlers                                                 */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0IsKernelAddress
 * @purpose    Check if physical address maps to kernel space
 * @returns    TRUE if address is in kernel range
 */
__inline BOOLEAN Sr0IsKernelAddress(_In_ PHYSICAL_ADDRESS PhysAddr) {
  return PhysAddr.QuadPart >= SR0_KERNEL_ADDRESS_START;
}

/**
 * @function   Sr0HandleReadMemory
 * @purpose    Read physical memory with kernel address blocking
 * @returns    STATUS_SUCCESS with data, or STATUS_ACCESS_DENIED
 */
static NTSTATUS Sr0HandleReadMemory(_In_ PIRP Irp,
                                    _In_ PIO_STACK_LOCATION IrpSp,
                                    _In_ ULONG ProcessId) {
  POLS_READ_MEMORY_INPUT input;
  PUCHAR outputBuffer;
  SR0_RATE_CHECK_RESULT rateResult;
  PVOID mappedAddr;
  ULONG totalSize;

  if (IrpSp->Parameters.DeviceIoControl.InputBufferLength <
      sizeof(OLS_READ_MEMORY_INPUT)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  input = (POLS_READ_MEMORY_INPUT)Irp->AssociatedIrp.SystemBuffer;
  totalSize = input->UnitSize * input->Count;

  /* Validate unit size */
  if (input->UnitSize != 1 && input->UnitSize != 2 && input->UnitSize != 4) {
    Irp->IoStatus.Information = 0;
    return STATUS_INVALID_PARAMETER;
  }

  /* Validate total size */
  if (totalSize == 0 || totalSize > SR0_MAX_MAP_SIZE) {
    Irp->IoStatus.Information = 0;
    return STATUS_INVALID_PARAMETER;
  }

  if (IrpSp->Parameters.DeviceIoControl.OutputBufferLength < totalSize) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  /* Block kernel address mapping - security critical */
  if (Sr0IsKernelAddress(input->Address)) {
    Sr0EtwLogMemoryBlocked(ProcessId, input->Address.QuadPart,
                           "Kernel address range");
    Irp->IoStatus.Information = 0;
    return STATUS_ACCESS_DENIED;
  }

  /* Rate limit check */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    Irp->IoStatus.Information = 0;
    return STATUS_QUOTA_EXCEEDED;
  }

  /* Map physical memory */
  mappedAddr = MmMapIoSpace(input->Address, totalSize, MmNonCached);
  if (mappedAddr == NULL) {
    Irp->IoStatus.Information = 0;
    return STATUS_INSUFFICIENT_RESOURCES;
  }

  /* Copy data to output buffer */
  outputBuffer = (PUCHAR)Irp->AssociatedIrp.SystemBuffer;
  RtlCopyMemory(outputBuffer, mappedAddr, totalSize);
  MmUnmapIoSpace(mappedAddr, totalSize);

  Sr0EtwLogMemoryRead(ProcessId, input->Address.QuadPart, totalSize);
  Irp->IoStatus.Information = totalSize;
  return STATUS_SUCCESS;
}

/**
 * @function   Sr0HandleWriteMemory
 * @purpose    Write physical memory with kernel address blocking
 * @returns    STATUS_SUCCESS, or STATUS_ACCESS_DENIED
 */
static NTSTATUS Sr0HandleWriteMemory(_In_ PIRP Irp,
                                     _In_ PIO_STACK_LOCATION IrpSp,
                                     _In_ ULONG ProcessId) {
  POLS_WRITE_MEMORY_INPUT input;
  SR0_RATE_CHECK_RESULT rateResult;
  PVOID mappedAddr;
  ULONG totalSize;
  ULONG headerSize;
  ULONG inputLen;

  headerSize = FIELD_OFFSET(OLS_WRITE_MEMORY_INPUT, Data);
  inputLen = IrpSp->Parameters.DeviceIoControl.InputBufferLength;

  if (inputLen < headerSize) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  input = (POLS_WRITE_MEMORY_INPUT)Irp->AssociatedIrp.SystemBuffer;
  totalSize = input->UnitSize * input->Count;

  /* Validate unit size */
  if (input->UnitSize != 1 && input->UnitSize != 2 && input->UnitSize != 4) {
    Irp->IoStatus.Information = 0;
    return STATUS_INVALID_PARAMETER;
  }

  /* Validate total size */
  if (totalSize == 0 || totalSize > SR0_MAX_MAP_SIZE) {
    Irp->IoStatus.Information = 0;
    return STATUS_INVALID_PARAMETER;
  }

  /* Validate input buffer contains enough data */
  if (inputLen < headerSize + totalSize) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  /* Block kernel address mapping */
  if (Sr0IsKernelAddress(input->Address)) {
    Sr0EtwLogMemoryBlocked(ProcessId, input->Address.QuadPart,
                           "Kernel address range");
    Irp->IoStatus.Information = 0;
    return STATUS_ACCESS_DENIED;
  }

  /* Rate limit check */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    Irp->IoStatus.Information = 0;
    return STATUS_QUOTA_EXCEEDED;
  }

  /* Map physical memory */
  mappedAddr = MmMapIoSpace(input->Address, totalSize, MmNonCached);
  if (mappedAddr == NULL) {
    Irp->IoStatus.Information = 0;
    return STATUS_INSUFFICIENT_RESOURCES;
  }

  /* Write data */
  RtlCopyMemory(mappedAddr, input->Data, totalSize);
  MmUnmapIoSpace(mappedAddr, totalSize);

  Sr0EtwLogMemoryWrite(ProcessId, input->Address.QuadPart, totalSize);
  Irp->IoStatus.Information = 0;
  return STATUS_SUCCESS;
}

/*--------------------------------------------------------------------------*/
/* PCI Configuration Space Handlers                                         */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0HandleReadPciConfig
 * @purpose    Read PCI configuration space via port 0xCF8/0xCFC
 * @returns    STATUS_SUCCESS with DWORD value
 */
static NTSTATUS Sr0HandleReadPciConfig(_In_ PIRP Irp,
                                       _In_ PIO_STACK_LOCATION IrpSp,
                                       _In_ ULONG ProcessId) {
  POLS_READ_PCI_CONFIG_INPUT input;
  PULONG outputBuffer;
  SR0_RATE_CHECK_RESULT rateResult;
  ULONG address;

  if (IrpSp->Parameters.DeviceIoControl.InputBufferLength <
      sizeof(OLS_READ_PCI_CONFIG_INPUT)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  if (IrpSp->Parameters.DeviceIoControl.OutputBufferLength < sizeof(ULONG)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  /* Rate limit check */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    Irp->IoStatus.Information = 0;
    return STATUS_QUOTA_EXCEEDED;
  }

  input = (POLS_READ_PCI_CONFIG_INPUT)Irp->AssociatedIrp.SystemBuffer;
  outputBuffer = (PULONG)Irp->AssociatedIrp.SystemBuffer;

  /* Build PCI address with enable bit */
  address = (input->PciAddress & 0xFFFFFF00) | (input->PciOffset & 0xFC);
  address |= 0x80000000; /* Enable bit */

  /* PCI config access via CF8/CFC ports */
  __outdword(PCI_CONFIG_ADDRESS_PORT, address);
  *outputBuffer = __indword(PCI_CONFIG_DATA_PORT);

  Sr0EtwLogPciRead(ProcessId, PCI_GET_BUS(input->PciAddress),
                   (UCHAR)PCI_GET_DEV(input->PciAddress),
                   (UCHAR)PCI_GET_FUNC(input->PciAddress),
                   (USHORT)input->PciOffset);

  Irp->IoStatus.Information = sizeof(ULONG);
  return STATUS_SUCCESS;
}

/**
 * @function   Sr0HandleWritePciConfig
 * @purpose    Write PCI configuration space via port 0xCF8/0xCFC
 * @returns    STATUS_SUCCESS on success
 */
static NTSTATUS Sr0HandleWritePciConfig(_In_ PIRP Irp,
                                        _In_ PIO_STACK_LOCATION IrpSp,
                                        _In_ ULONG ProcessId) {
  POLS_WRITE_PCI_CONFIG_INPUT input;
  SR0_RATE_CHECK_RESULT rateResult;
  ULONG address;
  ULONG headerSize;
  ULONG inputLen;
  ULONG value;

  headerSize = FIELD_OFFSET(OLS_WRITE_PCI_CONFIG_INPUT, Data);
  inputLen = IrpSp->Parameters.DeviceIoControl.InputBufferLength;

  if (inputLen < headerSize + sizeof(ULONG)) {
    Irp->IoStatus.Information = 0;
    return STATUS_BUFFER_TOO_SMALL;
  }

  /* Rate limit check */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    Irp->IoStatus.Information = 0;
    return STATUS_QUOTA_EXCEEDED;
  }

  input = (POLS_WRITE_PCI_CONFIG_INPUT)Irp->AssociatedIrp.SystemBuffer;
  value = *(PULONG)input->Data;

  /* Build PCI address with enable bit */
  address = (input->PciAddress & 0xFFFFFF00) | (input->PciOffset & 0xFC);
  address |= 0x80000000; /* Enable bit */

  /* PCI config access via CF8/CFC ports */
  __outdword(PCI_CONFIG_ADDRESS_PORT, address);
  __outdword(PCI_CONFIG_DATA_PORT, value);

  Sr0EtwLogPciWrite(ProcessId, PCI_GET_BUS(input->PciAddress),
                    (UCHAR)PCI_GET_DEV(input->PciAddress),
                    (UCHAR)PCI_GET_FUNC(input->PciAddress),
                    (USHORT)input->PciOffset);

  Irp->IoStatus.Information = 0;
  return STATUS_SUCCESS;
}
