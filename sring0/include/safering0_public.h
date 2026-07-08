#pragma once

/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: safering0_public.h
 * Version: 1.0
 * Date: 2025-12-04
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 * SafeRing0 - Shared Public Contracts
 *
 * Exposes device naming, IOCTL-visible structures, and capability flags used
 * by both kernel-mode driver and user-mode DLL. This header is safe to include
 * from either environment; it selects the appropriate base definitions
 * automatically.
 *
 * Security:
 * - This is the primary fix for CVE-2020-14979 (WinRing0 lacks device ACL)
 * - Device ACL restricts access to SYSTEM and Administrators only
 * - Tiered MSR policy: reads allowed, writes denied by default
 * - Physical memory validation blocks kernel address mapping
 */

/*
 * Include order matters:
 * 1. safering0_version.h - No dependencies
 * 2. safering0_ioctl.h   - Includes platform headers (ntddk.h / windows.h)
 */
#include "safering0_ioctl.h"
#include "safering0_version.h"

#ifdef __cplusplus
extern "C" {
#endif

/*--------------------------------------------------------------------------*/
/* Device Identity                                                          */
/*--------------------------------------------------------------------------*/

/*
 * Device names match WinRing0 for drop-in compatibility.
 * Apps using \\.\WinRing0_1_2_0 will connect to SafeRing0.
 * Reference: OLS_DRIVER_VERSION = 1.2.0.5 in original WinRing0
 */
#define SR0_DEVICE_NAME_U L"\\Device\\WinRing0_1_2_0"
#define SR0_SYMLINK_NAME_U L"\\DosDevices\\WinRing0_1_2_0"
#define SR0_DOSLINK_U L"\\\\.\\WinRing0_1_2_0"

#define SR0_DEVICE_NAME_A "\\Device\\WinRing0_1_2_0"
#define SR0_SYMLINK_NAME_A "\\\\.\\WinRing0_1_2_0"

/*--------------------------------------------------------------------------*/
/* Capability Flags                                                         */
/*--------------------------------------------------------------------------*/

#define SR0_CAP_MSR_READ 0x00000001u        /* MSR read operations */
#define SR0_CAP_MSR_WRITE 0x00000002u       /* MSR write (tiered policy) */
#define SR0_CAP_IO_PORT 0x00000004u         /* I/O port read/write */
#define SR0_CAP_PHYS_MEMORY 0x00000008u     /* Physical memory access */
#define SR0_CAP_PCI_CONFIG 0x00000010u      /* PCI configuration space */
#define SR0_CAP_RATE_LIMIT 0x00000020u      /* Per-process rate limiting */
#define SR0_CAP_ETW_TELEMETRY 0x00000040u   /* ETW TraceLogging events */
#define SR0_CAP_MSR_WRITE_OPTIN 0x00000080u /* MSR write opt-in enabled */

/*--------------------------------------------------------------------------*/
/* Status Codes (DLL)                                                       */
/*--------------------------------------------------------------------------*/

/*
 * DLL status codes returned by GetDllStatus()
 * Matches WinRing0 for compatibility
 */
#define OLS_DLL_NO_ERROR 0
#define OLS_DLL_UNSUPPORTED_PLATFORM 1
#define OLS_DLL_DRIVER_NOT_LOADED 2
#define OLS_DLL_DRIVER_NOT_FOUND 3
#define OLS_DLL_DRIVER_UNLOADED 4
#define OLS_DLL_DRIVER_NOT_LOADED_ON_NETWORK 5
#define OLS_DLL_UNKNOWN_ERROR 9

/*
 * Driver type returned by GetDriverType()
 */
#define OLS_DRIVER_TYPE_UNKNOWN 0
#define OLS_DRIVER_TYPE_WIN_9X 1
#define OLS_DRIVER_TYPE_WIN_NT 2
#define OLS_DRIVER_TYPE_WIN_NT_X64 3
#define OLS_DRIVER_TYPE_WIN_NT4 4

/*--------------------------------------------------------------------------*/
/* MSR Policy Definitions                                                   */
/*--------------------------------------------------------------------------*/

/*
 * Registry key for MSR write opt-in.
 * When set to 1, MSR writes to whitelisted registers are allowed.
 * Default: 0 (all MSR writes denied)
 */
#define SR0_MSR_OPTIN_REGKEY \
  L"SYSTEM\\CurrentControlSet\\Services\\SafeRing0\\Parameters"
#define SR0_MSR_OPTIN_VALUE L"AllowMsrWrites"

/*
 * MSR Policy Result Codes
 */
typedef enum _SR0_MSR_POLICY_RESULT {
  Sr0MsrAllowed = 0,          /* Operation permitted */
  Sr0MsrDeniedNoOptIn,        /* Writes disabled (default) */
  Sr0MsrDeniedNotWhitelisted, /* Not in whitelist (opt-in mode) */
  Sr0MsrDeniedNeverWritable   /* Syscall/interrupt MSR - always blocked */
} SR0_MSR_POLICY_RESULT;

/*--------------------------------------------------------------------------*/
/* Rate Limiting Configuration                                              */
/*--------------------------------------------------------------------------*/

#define SR0_DEFAULT_RATE_LIMIT_PER_PROCESS 100 /* ops/sec per process */
#define SR0_DEFAULT_RATE_LIMIT_GLOBAL 1000     /* ops/sec global */

/*
 * Rate limit result codes
 */
typedef enum _SR0_RATE_RESULT {
  Sr0RateAllowed = 0,     /* Operation permitted */
  Sr0RateExceededProcess, /* Per-process limit exceeded */
  Sr0RateExceededGlobal   /* Global limit exceeded */
} SR0_RATE_RESULT;

/*--------------------------------------------------------------------------*/
/* Physical Memory Limits                                                   */
/*--------------------------------------------------------------------------*/

#define SR0_MAX_MAP_SIZE (16 * 1024 * 1024) /* 16 MB max mapping */
#define SR0_KERNEL_ADDRESS_START 0xFFFF800000000000ULL

/*--------------------------------------------------------------------------*/
/* ETW Provider                                                             */
/*--------------------------------------------------------------------------*/

/*
 * ETW Provider GUID for SafeRing0
 * Use: logman query providers | findstr SafeRing0
 */
/* {A1B2C3D4-E5F6-7890-ABCD-EF1234567890} */
#define SR0_ETW_PROVIDER_GUID_STR L"{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}"

/*
 * ETW Event IDs
 */
#define SR0_ETW_EVENT_MSR_READ 1
#define SR0_ETW_EVENT_MSR_WRITE 2
#define SR0_ETW_EVENT_MSR_BLOCKED 3
#define SR0_ETW_EVENT_MEMORY_READ 10
#define SR0_ETW_EVENT_MEMORY_WRITE 11
#define SR0_ETW_EVENT_MEMORY_BLOCKED 12
#define SR0_ETW_EVENT_IO_PORT_READ 20
#define SR0_ETW_EVENT_IO_PORT_WRITE 21
#define SR0_ETW_EVENT_PCI_READ 30
#define SR0_ETW_EVENT_PCI_WRITE 31
#define SR0_ETW_EVENT_RATE_EXCEEDED 40
#define SR0_ETW_EVENT_DRIVER_LOAD 100
#define SR0_ETW_EVENT_DRIVER_UNLOAD 101

/*
 * ETW Keywords for filtering
 */
#define SR0_ETW_KEYWORD_MSR 0x0001
#define SR0_ETW_KEYWORD_MEMORY 0x0002
#define SR0_ETW_KEYWORD_IOPORT 0x0004
#define SR0_ETW_KEYWORD_PCI 0x0008
#define SR0_ETW_KEYWORD_BLOCKED 0x0010
#define SR0_ETW_KEYWORD_RATELIMIT 0x0020
#define SR0_ETW_KEYWORD_LIFECYCLE 0x0040

/*--------------------------------------------------------------------------*/
/* Pool Tags (Driver Internal)                                              */
/*--------------------------------------------------------------------------*/

#ifdef _KERNEL_MODE
#define SR0_POOL_TAG_GENERAL 'Sr0G' /* General allocations */
#define SR0_POOL_TAG_RATE 'Sr0R'    /* Rate limit entries */
#define SR0_POOL_TAG_MAP 'Sr0M'     /* Memory mapping tracking */
#endif

/*--------------------------------------------------------------------------*/
/* SDDL String (Device Security)                                            */
/*--------------------------------------------------------------------------*/

/*
 * SDDL: Only SYSTEM (SY) and Administrators (BA) get full access.
 * This is THE fix for CVE-2020-14979.
 *
 * D:P           - DACL, protected
 * (A;;GA;;;SY)  - Allow Generic All to SYSTEM
 * (A;;GA;;;BA)  - Allow Generic All to Built-in Administrators
 */
#define SR0_SDDL_STRING L"D:P(A;;GA;;;SY)(A;;GA;;;BA)"

#ifdef __cplusplus
}
#endif
