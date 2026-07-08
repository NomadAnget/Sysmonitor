/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: telemetry.c
 * Version: 1.0
 * Date: 2025-12-04
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - ETW TraceLogging Provider
 *   Provides structured telemetry for security monitoring and forensics.
 *   Uses TraceLogging API for manifest-free event emission.
 *
 * Security:
 *   - Logs all blocked operations for security analysis
 *   - Includes caller PID for attribution
 *   - Keywords enable selective monitoring
 *
 * Threading Model:
 *   - TraceLogging is thread-safe
 *   - Events can be emitted at IRQL <= HIGH_LEVEL
 *   - No allocations in hot path
 *
 * Usage:
 *   Consume events via: logman query providers | findstr SafeRing0
 *   Or: wevtutil qe "Microsoft-Windows-SafeRing0/Operational" /f:text
 */

#include <ntddk.h>

/*
 * TraceLogging requires these headers in specific order:
 * 1. ntddk.h first (defines ULONG, etc.)
 * 2. evntrace.h for ETW support
 * 3. TraceLoggingProvider.h for TraceLogging macros
 */
#include <TraceLoggingProvider.h>
#include <evntrace.h>

#include "..\..\include\safering0_public.h"
#include "safering0_main.h"

/*--------------------------------------------------------------------------*/
/* ETW Provider Definition                                                  */
/*--------------------------------------------------------------------------*/

/*
 * Define the TraceLogging provider.
 * GUID: {A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
 * Name: SafeRing0
 */
TRACELOGGING_DEFINE_PROVIDER(g_Sr0EtwProvider, "SafeRing0",
                             (0xa1b2c3d4, 0xe5f6, 0x7890, 0xab, 0xcd, 0xef,
                              0x12, 0x34, 0x56, 0x78, 0x90));

static BOOLEAN g_EtwInitialized = FALSE;

/*--------------------------------------------------------------------------*/
/* Initialization / Shutdown                                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0EtwInitialize
 * @purpose    Register ETW TraceLogging provider
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition Provider registered, events can be emitted
 * @returns    STATUS_SUCCESS or error from TraceLoggingRegister
 * @thread-safety Single-threaded init only
 * @side-effects Registers ETW provider with Windows
 */
_Use_decl_annotations_ NTSTATUS Sr0EtwInitialize(VOID) {
  NTSTATUS status;

  status = TraceLoggingRegister(g_Sr0EtwProvider);
  if (!NT_SUCCESS(status)) {
    return status;
  }

  g_EtwInitialized = TRUE;

  /* Log driver load event */
  TraceLoggingWrite(g_Sr0EtwProvider, "DriverLoad",
                    TraceLoggingLevel(TRACE_LEVEL_INFORMATION),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_LIFECYCLE),
                    TraceLoggingUInt32(SR0_VERSION_MAJOR, "VersionMajor"),
                    TraceLoggingUInt32(SR0_VERSION_MINOR, "VersionMinor"),
                    TraceLoggingUInt32(SR0_VERSION_REVISION, "VersionRevision"),
                    TraceLoggingUInt32(SR0_VERSION_BUILD, "VersionBuild"));

  return STATUS_SUCCESS;
}

/**
 * @function   Sr0EtwShutdown
 * @purpose    Unregister ETW TraceLogging provider
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition Provider unregistered
 * @thread-safety Single-threaded shutdown only
 * @side-effects Unregisters ETW provider
 */
_Use_decl_annotations_ VOID Sr0EtwShutdown(VOID) {
  if (!g_EtwInitialized) {
    return;
  }

  /* Log driver unload event */
  TraceLoggingWrite(g_Sr0EtwProvider, "DriverUnload",
                    TraceLoggingLevel(TRACE_LEVEL_INFORMATION),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_LIFECYCLE));

  TraceLoggingUnregister(g_Sr0EtwProvider);
  g_EtwInitialized = FALSE;
}

/*--------------------------------------------------------------------------*/
/* MSR Events                                                               */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0EtwLogMsrRead
 * @purpose    Log MSR read operation
 * @param      ProcessId - Caller process ID
 * @param      MsrIndex - MSR register index
 * @param      Value - Value read from MSR
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogMsrRead(_In_ ULONG ProcessId, _In_ ULONG MsrIndex,
                      _In_ ULONG64 Value) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(g_Sr0EtwProvider, "MsrRead",
                    TraceLoggingLevel(TRACE_LEVEL_VERBOSE),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_MSR),
                    TraceLoggingUInt32(ProcessId, "ProcessId"),
                    TraceLoggingHexUInt32(MsrIndex, "MsrIndex"),
                    TraceLoggingHexUInt64(Value, "Value"));
}

/**
 * @function   Sr0EtwLogMsrWrite
 * @purpose    Log MSR write operation
 * @param      ProcessId - Caller process ID
 * @param      MsrIndex - MSR register index
 * @param      Value - Value written to MSR
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogMsrWrite(_In_ ULONG ProcessId, _In_ ULONG MsrIndex,
                       _In_ ULONG64 Value) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(g_Sr0EtwProvider, "MsrWrite",
                    TraceLoggingLevel(TRACE_LEVEL_INFORMATION),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_MSR),
                    TraceLoggingUInt32(ProcessId, "ProcessId"),
                    TraceLoggingHexUInt32(MsrIndex, "MsrIndex"),
                    TraceLoggingHexUInt64(Value, "Value"));
}

/**
 * @function   Sr0EtwLogMsrBlocked
 * @purpose    Log blocked MSR write operation (security event)
 * @param      ProcessId - Caller process ID
 * @param      MsrIndex - MSR register index
 * @param      Value - Attempted value
 * @param      Reason - Denial reason string
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event at WARNING level
 */
VOID Sr0EtwLogMsrBlocked(_In_ ULONG ProcessId, _In_ ULONG MsrIndex,
                         _In_ ULONG64 Value, _In_ PCSTR Reason) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(
      g_Sr0EtwProvider, "MsrBlocked", TraceLoggingLevel(TRACE_LEVEL_WARNING),
      TraceLoggingKeyword(SR0_ETW_KEYWORD_MSR | SR0_ETW_KEYWORD_BLOCKED),
      TraceLoggingUInt32(ProcessId, "ProcessId"),
      TraceLoggingHexUInt32(MsrIndex, "MsrIndex"),
      TraceLoggingHexUInt64(Value, "AttemptedValue"),
      TraceLoggingString(Reason, "Reason"));
}

/*--------------------------------------------------------------------------*/
/* Memory Events                                                            */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0EtwLogMemoryRead
 * @purpose    Log physical memory read operation
 * @param      ProcessId - Caller process ID
 * @param      PhysicalAddress - Physical address read
 * @param      Size - Size of read in bytes
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogMemoryRead(_In_ ULONG ProcessId, _In_ ULONG64 PhysicalAddress,
                         _In_ ULONG Size) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(g_Sr0EtwProvider, "MemoryRead",
                    TraceLoggingLevel(TRACE_LEVEL_VERBOSE),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_MEMORY),
                    TraceLoggingUInt32(ProcessId, "ProcessId"),
                    TraceLoggingHexUInt64(PhysicalAddress, "PhysicalAddress"),
                    TraceLoggingUInt32(Size, "Size"));
}

/**
 * @function   Sr0EtwLogMemoryWrite
 * @purpose    Log physical memory write operation
 * @param      ProcessId - Caller process ID
 * @param      PhysicalAddress - Physical address written
 * @param      Size - Size of write in bytes
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogMemoryWrite(_In_ ULONG ProcessId, _In_ ULONG64 PhysicalAddress,
                          _In_ ULONG Size) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(g_Sr0EtwProvider, "MemoryWrite",
                    TraceLoggingLevel(TRACE_LEVEL_INFORMATION),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_MEMORY),
                    TraceLoggingUInt32(ProcessId, "ProcessId"),
                    TraceLoggingHexUInt64(PhysicalAddress, "PhysicalAddress"),
                    TraceLoggingUInt32(Size, "Size"));
}

/**
 * @function   Sr0EtwLogMemoryBlocked
 * @purpose    Log blocked memory access (kernel address)
 * @param      ProcessId - Caller process ID
 * @param      PhysicalAddress - Attempted physical address
 * @param      Reason - Denial reason string
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event at WARNING level
 */
VOID Sr0EtwLogMemoryBlocked(_In_ ULONG ProcessId, _In_ ULONG64 PhysicalAddress,
                            _In_ PCSTR Reason) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(
      g_Sr0EtwProvider, "MemoryBlocked", TraceLoggingLevel(TRACE_LEVEL_WARNING),
      TraceLoggingKeyword(SR0_ETW_KEYWORD_MEMORY | SR0_ETW_KEYWORD_BLOCKED),
      TraceLoggingUInt32(ProcessId, "ProcessId"),
      TraceLoggingHexUInt64(PhysicalAddress, "PhysicalAddress"),
      TraceLoggingString(Reason, "Reason"));
}

/*--------------------------------------------------------------------------*/
/* I/O Port Events                                                          */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0EtwLogIoPortRead
 * @purpose    Log I/O port read operation
 * @param      ProcessId - Caller process ID
 * @param      PortNumber - I/O port address
 * @param      Value - Value read
 * @param      Size - Access size (1, 2, or 4 bytes)
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogIoPortRead(_In_ ULONG ProcessId, _In_ USHORT PortNumber,
                         _In_ ULONG Value, _In_ UCHAR Size) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(
      g_Sr0EtwProvider, "IoPortRead", TraceLoggingLevel(TRACE_LEVEL_VERBOSE),
      TraceLoggingKeyword(SR0_ETW_KEYWORD_IOPORT),
      TraceLoggingUInt32(ProcessId, "ProcessId"),
      TraceLoggingHexUInt16(PortNumber, "Port"),
      TraceLoggingHexUInt32(Value, "Value"), TraceLoggingUInt8(Size, "Size"));
}

/**
 * @function   Sr0EtwLogIoPortWrite
 * @purpose    Log I/O port write operation
 * @param      ProcessId - Caller process ID
 * @param      PortNumber - I/O port address
 * @param      Value - Value written
 * @param      Size - Access size (1, 2, or 4 bytes)
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogIoPortWrite(_In_ ULONG ProcessId, _In_ USHORT PortNumber,
                          _In_ ULONG Value, _In_ UCHAR Size) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(
      g_Sr0EtwProvider, "IoPortWrite", TraceLoggingLevel(TRACE_LEVEL_VERBOSE),
      TraceLoggingKeyword(SR0_ETW_KEYWORD_IOPORT),
      TraceLoggingUInt32(ProcessId, "ProcessId"),
      TraceLoggingHexUInt16(PortNumber, "Port"),
      TraceLoggingHexUInt32(Value, "Value"), TraceLoggingUInt8(Size, "Size"));
}

/*--------------------------------------------------------------------------*/
/* PCI Events                                                               */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0EtwLogPciRead
 * @purpose    Log PCI config space read operation
 * @param      ProcessId - Caller process ID
 * @param      Bus - PCI bus number
 * @param      Device - PCI device number
 * @param      Function - PCI function number
 * @param      Offset - Config space offset (0-4095 for PCIe extended)
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogPciRead(_In_ ULONG ProcessId, _In_ UCHAR Bus, _In_ UCHAR Device,
                      _In_ UCHAR Function, _In_ USHORT Offset) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(
      g_Sr0EtwProvider, "PciRead", TraceLoggingLevel(TRACE_LEVEL_VERBOSE),
      TraceLoggingKeyword(SR0_ETW_KEYWORD_PCI),
      TraceLoggingUInt32(ProcessId, "ProcessId"), TraceLoggingUInt8(Bus, "Bus"),
      TraceLoggingUInt8(Device, "Device"),
      TraceLoggingUInt8(Function, "Function"),
      TraceLoggingHexUInt16(Offset, "Offset"));
}

/**
 * @function   Sr0EtwLogPciWrite
 * @purpose    Log PCI config space write operation
 * @param      ProcessId - Caller process ID
 * @param      Bus - PCI bus number
 * @param      Device - PCI device number
 * @param      Function - PCI function number
 * @param      Offset - Config space offset (0-4095 for PCIe extended)
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event
 */
VOID Sr0EtwLogPciWrite(_In_ ULONG ProcessId, _In_ UCHAR Bus, _In_ UCHAR Device,
                       _In_ UCHAR Function, _In_ USHORT Offset) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(
      g_Sr0EtwProvider, "PciWrite", TraceLoggingLevel(TRACE_LEVEL_VERBOSE),
      TraceLoggingKeyword(SR0_ETW_KEYWORD_PCI),
      TraceLoggingUInt32(ProcessId, "ProcessId"), TraceLoggingUInt8(Bus, "Bus"),
      TraceLoggingUInt8(Device, "Device"),
      TraceLoggingUInt8(Function, "Function"),
      TraceLoggingHexUInt16(Offset, "Offset"));
}

/*--------------------------------------------------------------------------*/
/* Rate Limit Events                                                        */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0EtwLogRateExceeded
 * @purpose    Log rate limit exceeded event
 * @param      ProcessId - Caller process ID
 * @param      IsGlobal - TRUE if global limit, FALSE if per-process
 * @precondition ETW initialized
 * @thread-safety Thread-safe
 * @side-effects Emits ETW event at WARNING level
 */
VOID Sr0EtwLogRateExceeded(_In_ ULONG ProcessId, _In_ BOOLEAN IsGlobal) {
  if (!g_EtwInitialized) {
    return;
  }

  TraceLoggingWrite(g_Sr0EtwProvider, "RateExceeded",
                    TraceLoggingLevel(TRACE_LEVEL_WARNING),
                    TraceLoggingKeyword(SR0_ETW_KEYWORD_RATELIMIT),
                    TraceLoggingUInt32(ProcessId, "ProcessId"),
                    TraceLoggingBool(IsGlobal, "IsGlobalLimit"));
}
