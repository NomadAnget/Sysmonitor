#pragma once

/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: safering0_ioctl.h
 * Version: 1.0
 * Date: 2025-12-04
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 * SafeRing0 - WinRing0-Compatible IOCTL Definitions
 *
 * IOCTL codes match the original WinRing0 driver for drop-in compatibility.
 * All IOCTLs use METHOD_BUFFERED for security (no raw user-mode pointers).
 *
 * Reference: https://github.com/openhardwaremonitor/openhardwaremonitor
 *            /blob/master/External/WinRing0/OlsIoctl.h
 *
 * Security:
 * - METHOD_BUFFERED prevents CWE-781 (improper address validation)
 * - Device ACL restricts access to SYSTEM and Administrators
 */

#ifdef _KERNEL_MODE
#include <ntddk.h>
#else
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <winioctl.h>
/* PHYSICAL_ADDRESS is only defined in kernel mode; provide user-mode equivalent
 */
typedef LARGE_INTEGER PHYSICAL_ADDRESS;
#endif

#ifdef __cplusplus
extern "C" {
#endif

/*--------------------------------------------------------------------------*/
/* Device Type                                                              */
/*--------------------------------------------------------------------------*/

/*
 * WinRing0 uses device type 40000 (0x9C40).
 * We maintain this for compatibility.
 */
#define OLS_TYPE 40000

/*--------------------------------------------------------------------------*/
/* Driver Information IOCTLs (0x800-0x80F)                                  */
/*--------------------------------------------------------------------------*/

#define IOCTL_OLS_GET_DRIVER_VERSION \
  CTL_CODE(OLS_TYPE, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_OLS_GET_REFCOUNT \
  CTL_CODE(OLS_TYPE, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)

/*--------------------------------------------------------------------------*/
/* MSR IOCTLs (0x820-0x82F)                                                 */
/*--------------------------------------------------------------------------*/

#define IOCTL_OLS_READ_MSR \
  CTL_CODE(OLS_TYPE, 0x821, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_OLS_WRITE_MSR \
  CTL_CODE(OLS_TYPE, 0x822, METHOD_BUFFERED, FILE_ANY_ACCESS)

#define IOCTL_OLS_READ_PMC \
  CTL_CODE(OLS_TYPE, 0x823, METHOD_BUFFERED, FILE_ANY_ACCESS)

/*
 * IOCTL_OLS_HALT (0x824) - BLOCKED
 * This IOCTL halts the CPU. No legitimate use case exists for user-mode
 * applications. SafeRing0 returns STATUS_ACCESS_DENIED.
 */
#define IOCTL_OLS_HALT \
  CTL_CODE(OLS_TYPE, 0x824, METHOD_BUFFERED, FILE_ANY_ACCESS)

/*--------------------------------------------------------------------------*/
/* I/O Port IOCTLs (0x830-0x83F)                                            */
/*--------------------------------------------------------------------------*/

/* Generic I/O port read/write (variable size) */
#define IOCTL_OLS_READ_IO_PORT \
  CTL_CODE(OLS_TYPE, 0x831, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_OLS_WRITE_IO_PORT \
  CTL_CODE(OLS_TYPE, 0x832, METHOD_BUFFERED, FILE_WRITE_ACCESS)

/* Typed I/O port read */
#define IOCTL_OLS_READ_IO_PORT_BYTE \
  CTL_CODE(OLS_TYPE, 0x833, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_OLS_READ_IO_PORT_WORD \
  CTL_CODE(OLS_TYPE, 0x834, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_OLS_READ_IO_PORT_DWORD \
  CTL_CODE(OLS_TYPE, 0x835, METHOD_BUFFERED, FILE_READ_ACCESS)

/* Typed I/O port write */
#define IOCTL_OLS_WRITE_IO_PORT_BYTE \
  CTL_CODE(OLS_TYPE, 0x836, METHOD_BUFFERED, FILE_WRITE_ACCESS)

#define IOCTL_OLS_WRITE_IO_PORT_WORD \
  CTL_CODE(OLS_TYPE, 0x837, METHOD_BUFFERED, FILE_WRITE_ACCESS)

#define IOCTL_OLS_WRITE_IO_PORT_DWORD \
  CTL_CODE(OLS_TYPE, 0x838, METHOD_BUFFERED, FILE_WRITE_ACCESS)

/*--------------------------------------------------------------------------*/
/* Physical Memory IOCTLs (0x840-0x84F)                                     */
/*--------------------------------------------------------------------------*/

#define IOCTL_OLS_READ_MEMORY \
  CTL_CODE(OLS_TYPE, 0x841, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_OLS_WRITE_MEMORY \
  CTL_CODE(OLS_TYPE, 0x842, METHOD_BUFFERED, FILE_WRITE_ACCESS)

/*--------------------------------------------------------------------------*/
/* PCI Configuration IOCTLs (0x850-0x85F)                                   */
/*--------------------------------------------------------------------------*/

#define IOCTL_OLS_READ_PCI_CONFIG \
  CTL_CODE(OLS_TYPE, 0x851, METHOD_BUFFERED, FILE_READ_ACCESS)

#define IOCTL_OLS_WRITE_PCI_CONFIG \
  CTL_CODE(OLS_TYPE, 0x852, METHOD_BUFFERED, FILE_WRITE_ACCESS)

/*--------------------------------------------------------------------------*/
/* IOCTL Input/Output Structures                                            */
/*--------------------------------------------------------------------------*/

/*
 * Structure packing matches original WinRing0 for binary compatibility.
 * All structures use natural alignment within the pack(1) block.
 */
#pragma pack(push, 1)

/*
 * MSR Read Input Structure
 * Input for IOCTL_OLS_READ_MSR
 */
typedef struct _OLS_READ_MSR_INPUT {
  ULONG Register; /* MSR index to read */
} OLS_READ_MSR_INPUT, *POLS_READ_MSR_INPUT;

/*
 * MSR Write Input Structure
 * Input for IOCTL_OLS_WRITE_MSR
 * Uses ULARGE_INTEGER for EDX:EAX compatibility
 */
typedef struct _OLS_WRITE_MSR_INPUT {
  ULONG Register;       /* MSR index */
  ULARGE_INTEGER Value; /* Value to write (EDX:EAX) */
} OLS_WRITE_MSR_INPUT, *POLS_WRITE_MSR_INPUT;

/*
 * I/O Port Read Input Structure
 * Input for IOCTL_OLS_READ_IO_PORT[_BYTE|_WORD|_DWORD]
 */
typedef struct _OLS_READ_IO_PORT_INPUT {
  ULONG PortNumber; /* I/O port address (0-0xFFFF) */
} OLS_READ_IO_PORT_INPUT, *POLS_READ_IO_PORT_INPUT;

/*
 * I/O Port Write Input Structure
 * Input for IOCTL_OLS_WRITE_IO_PORT[_BYTE|_WORD|_DWORD]
 * Union provides typed access to the value
 */
typedef struct _OLS_WRITE_IO_PORT_INPUT {
  ULONG PortNumber; /* I/O port address (0-0xFFFF) */
  union {
    ULONG LongData;   /* DWORD value */
    USHORT ShortData; /* WORD value */
    UCHAR CharData;   /* BYTE value */
  } u;
} OLS_WRITE_IO_PORT_INPUT, *POLS_WRITE_IO_PORT_INPUT;

/*
 * Physical Memory Read Input Structure
 * Input for IOCTL_OLS_READ_MEMORY
 */
typedef struct _OLS_READ_MEMORY_INPUT {
  PHYSICAL_ADDRESS Address; /* Physical address to read */
  ULONG UnitSize;           /* Unit size: 1, 2, or 4 bytes */
  ULONG Count;              /* Number of units to read */
} OLS_READ_MEMORY_INPUT, *POLS_READ_MEMORY_INPUT;

/*
 * Physical Memory Write Input Structure
 * Input for IOCTL_OLS_WRITE_MEMORY
 * Variable-length: Data[] follows the header
 */
typedef struct _OLS_WRITE_MEMORY_INPUT {
  PHYSICAL_ADDRESS Address; /* Physical address to write */
  ULONG UnitSize;           /* Unit size: 1, 2, or 4 bytes */
  ULONG Count;              /* Number of units to write */
  UCHAR Data[1];            /* Variable-length data follows */
} OLS_WRITE_MEMORY_INPUT, *POLS_WRITE_MEMORY_INPUT;

/*
 * PCI Configuration Read Input Structure
 * Input for IOCTL_OLS_READ_PCI_CONFIG
 */
typedef struct _OLS_READ_PCI_CONFIG_INPUT {
  ULONG PciAddress; /* PCI address (bus/device/function encoded) */
  ULONG PciOffset;  /* Register offset within config space */
} OLS_READ_PCI_CONFIG_INPUT, *POLS_READ_PCI_CONFIG_INPUT;

/*
 * PCI Configuration Write Input Structure
 * Input for IOCTL_OLS_WRITE_PCI_CONFIG
 * Variable-length: Data[] follows the header
 */
typedef struct _OLS_WRITE_PCI_CONFIG_INPUT {
  ULONG PciAddress; /* PCI address (bus/device/function encoded) */
  ULONG PciOffset;  /* Register offset within config space */
  UCHAR Data[1];    /* Variable-length data follows */
} OLS_WRITE_PCI_CONFIG_INPUT, *POLS_WRITE_PCI_CONFIG_INPUT;

#pragma pack(pop)

/*--------------------------------------------------------------------------*/
/* PCI Address Encoding Macros (WinRing0 compatible)                        */
/*--------------------------------------------------------------------------*/

/*
 * PCI Configuration Address Format:
 * Bit 31:    Enable bit (must be 1)
 * Bits 30-24: Reserved
 * Bits 23-16: Bus number (0-255)
 * Bits 15-11: Device number (0-31)
 * Bits 10-8:  Function number (0-7)
 * Bits 7-2:   Register offset (DWORD aligned)
 * Bits 1-0:   Must be 0
 */
#define PCI_CONFIG_ADDRESS(bus, dev, func, reg)                    \
  (0x80000000UL | (((ULONG)(bus) & 0xFF) << 16) |                  \
   (((ULONG)(dev) & 0x1F) << 11) | (((ULONG)(func) & 0x07) << 8) | \
   ((ULONG)(reg) & 0xFC))

#define PCI_GET_BUS(address) (((address) >> 16) & 0xFF)
#define PCI_GET_DEV(address) (((address) >> 11) & 0x1F)
#define PCI_GET_FUNC(address) (((address) >> 8) & 0x07)
#define PCI_GET_REG(address) ((address) & 0xFF)

#ifdef __cplusplus
}
#endif
