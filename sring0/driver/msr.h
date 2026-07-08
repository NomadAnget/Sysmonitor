/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: msr.h
 * Version: 1.0
 * Date: 2025-11-14
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - MSR Access Policy Definitions
 *   Defines the tiered MSR policy: reads allowed, writes controlled via
 *   never-writable list (always blocked) and opt-in whitelist.
 *
 * Security:
 *   - Never-writable MSRs: Syscall/interrupt handlers - ALWAYS blocked
 *   - Opt-in whitelist: Performance/power MSRs - only if registry opt-in
 * enabled
 *   - Default mode: All reads allowed, all writes denied (safest)
 *
 * Threading Model:
 *   - Policy checks are read-only, thread-safe
 *   - MsrWriteOptIn cached at driver load, read via interlocked
 */

#pragma once

#ifndef _KERNEL_MODE
#error "This header is for kernel-mode only."
#endif

#include <ntddk.h>

#ifdef __cplusplus
extern "C" {
#endif

/*--------------------------------------------------------------------------*/
/* MSR Policy Result Codes                                                  */
/*--------------------------------------------------------------------------*/

/*
 * Result codes for MSR policy checks.
 * These provide specific denial reasons for logging/debugging.
 */
typedef enum _SR0_MSR_CHECK_RESULT {
  Sr0MsrCheckAllowed = 0,        /* Operation permitted */
  Sr0MsrCheckDeniedNoOptIn,      /* Writes disabled (default mode) */
  Sr0MsrCheckDeniedNotWhitelist, /* MSR not in opt-in whitelist */
  Sr0MsrCheckDeniedNeverWrite    /* MSR in never-writable list */
} SR0_MSR_CHECK_RESULT;

/*--------------------------------------------------------------------------*/
/* Never-Writable MSR List (Security-Critical)                              */
/*--------------------------------------------------------------------------*/

/*
 * These MSRs control syscall/interrupt entry points and critical CPU features.
 * Writing to these enables kernel code execution - ALWAYS BLOCKED regardless
 * of opt-in setting.
 *
 * Attack vectors if writable:
 * - IA32_LSTAR: Redirect syscall to attacker code (Ring-0 execution)
 * - IA32_EFER: Disable NX/SMEP protections
 * - SYSCFG: Modify memory type ranges
 */

/* Intel/AMD syscall entry points */
#define MSR_IA32_LSTAR 0xC0000082  /* Long mode syscall entry */
#define MSR_IA32_CSTAR 0xC0000083  /* Compat mode syscall entry */
#define MSR_IA32_STAR 0xC0000081   /* Syscall segment selectors */
#define MSR_IA32_SFMASK 0xC0000084 /* Syscall flag mask */

/* Legacy syscall entry (SYSENTER) */
#define MSR_IA32_SYSENTER_CS 0x00000174
#define MSR_IA32_SYSENTER_ESP 0x00000175
#define MSR_IA32_SYSENTER_EIP 0x00000176

/* Extended Feature Enable Register */
#define MSR_IA32_EFER 0xC0000080

/* Debug/Trace control */
#define MSR_IA32_DEBUGCTL 0x000001D9

/* Page Attribute Table */
#define MSR_IA32_PAT 0x00000277

/* Miscellaneous Enable */
#define MSR_IA32_MISC_ENABLE 0x000001A0

/* AMD-specific dangerous MSRs */
#define MSR_AMD_SYSCFG 0xC0010010      /* System configuration */
#define MSR_AMD_VM_HSAVE_PA 0xC0010117 /* VM host save area */

/* Intel VMX MSRs (entire 0x480-0x48F range is dangerous) */
#define MSR_IA32_VMX_BASIC 0x00000480
#define MSR_IA32_VMX_RANGE_START 0x00000480
#define MSR_IA32_VMX_RANGE_END 0x0000048F

/*--------------------------------------------------------------------------*/
/* Opt-In Writable MSR Whitelist                                            */
/*--------------------------------------------------------------------------*/

/*
 * These MSRs are safe(r) to write for legitimate use cases:
 * - Performance monitoring (HWiNFO, counters)
 * - Frequency control (ThrottleStop, Ryzen Master)
 * - Power management (undervolting tools)
 *
 * Supports both Intel and AMD platforms.
 * Only allowed when registry opt-in is enabled AND MSR is in this list.
 */

/* Performance Event Select (counter configuration) */
#define MSR_IA32_PERFEVTSEL0 0x00000186
#define MSR_IA32_PERFEVTSEL1 0x00000187
#define MSR_IA32_PERFEVTSEL2 0x00000188
#define MSR_IA32_PERFEVTSEL3 0x00000189

/* Performance Counters */
#define MSR_IA32_PMC0 0x000000C1
#define MSR_IA32_PMC1 0x000000C2
#define MSR_IA32_PMC2 0x000000C3
#define MSR_IA32_PMC3 0x000000C4

/* Fixed-Function Performance Counters */
#define MSR_IA32_FIXED_CTR0 0x00000309
#define MSR_IA32_FIXED_CTR1 0x0000030A
#define MSR_IA32_FIXED_CTR2 0x0000030B
#define MSR_IA32_FIXED_CTR_CTRL 0x0000038D

/* Performance Control */
#define MSR_IA32_PERF_CTL 0x00000199
#define MSR_IA32_PERF_STATUS 0x00000198

/* Energy/Power Management */
#define MSR_IA32_ENERGY_PERF_BIAS 0x000001B0
#define MSR_PKG_POWER_LIMIT 0x00000610
#define MSR_PP0_POWER_LIMIT 0x00000638
#define MSR_PP1_POWER_LIMIT 0x00000640

/* Turbo Ratio Limits (read-write on some CPUs) */
#define MSR_TURBO_RATIO_LIMIT 0x000001AD

/* HWP (Hardware P-States) */
#define MSR_IA32_HWP_REQUEST 0x00000774

/*--------------------------------------------------------------------------*/
/* AMD-Specific Whitelist MSRs                                              */
/*--------------------------------------------------------------------------*/

/*
 * AMD P-State Definition MSRs (0xC0010064-0xC001006B)
 * Used by ThrottleStop, Ryzen Master, and undervolting tools.
 * Each P-state defines voltage/frequency for that performance level.
 */
#define MSR_AMD_PSTATE_DEF_0 0xC0010064 /* P-State 0 (highest perf) */
#define MSR_AMD_PSTATE_DEF_1 0xC0010065 /* P-State 1 */
#define MSR_AMD_PSTATE_DEF_2 0xC0010066 /* P-State 2 */
#define MSR_AMD_PSTATE_DEF_3 0xC0010067 /* P-State 3 */
#define MSR_AMD_PSTATE_DEF_4 0xC0010068 /* P-State 4 */
#define MSR_AMD_PSTATE_DEF_5 0xC0010069 /* P-State 5 */
#define MSR_AMD_PSTATE_DEF_6 0xC001006A /* P-State 6 */
#define MSR_AMD_PSTATE_DEF_7 0xC001006B /* P-State 7 (lowest perf) */

/* AMD P-State Control/Status */
#define MSR_AMD_PSTATE_CTL 0xC0010062    /* P-State control */
#define MSR_AMD_PSTATE_STATUS 0xC0010063 /* P-State status (read-only) */

/* AMD Performance Event Select (counter configuration) */
#define MSR_AMD_PERFEVTSEL0 0xC0010000
#define MSR_AMD_PERFEVTSEL1 0xC0010001
#define MSR_AMD_PERFEVTSEL2 0xC0010002
#define MSR_AMD_PERFEVTSEL3 0xC0010003

/* AMD Performance Event Counters */
#define MSR_AMD_PERFCTR0 0xC0010004
#define MSR_AMD_PERFCTR1 0xC0010005
#define MSR_AMD_PERFCTR2 0xC0010006
#define MSR_AMD_PERFCTR3 0xC0010007

/*
 * AMD Core Energy Status (RAPL equivalent)
 * NOTE: These are READ-ONLY accumulator registers. Writes are ignored.
 * Not in whitelist since write capability is meaningless.
 */
#define MSR_AMD_CORE_ENERGY_STATUS 0xC001029A
#define MSR_AMD_PKG_ENERGY_STATUS 0xC001029B

/*--------------------------------------------------------------------------*/
/* MSR Access Functions                                                     */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0MsrInitialize
 * @purpose    Initialize MSR policy subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 * @returns    STATUS_SUCCESS always (no allocations needed)
 */
_IRQL_requires_(PASSIVE_LEVEL) NTSTATUS Sr0MsrInitialize(VOID);

/**
 * @function   Sr0MsrShutdown
 * @purpose    Shutdown MSR policy subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 */
_IRQL_requires_(PASSIVE_LEVEL) VOID Sr0MsrShutdown(VOID);

/**
 * @function   Sr0MsrCheckWritePolicy
 * @purpose    Check if MSR write is allowed by policy
 * @param      MsrIndex - MSR register index to check
 * @precondition None (can be called at any IRQL)
 * @returns    SR0_MSR_CHECK_RESULT indicating allow/deny reason
 * @thread-safety Thread-safe (read-only policy check)
 */
_IRQL_requires_max_(DISPATCH_LEVEL) SR0_MSR_CHECK_RESULT
    Sr0MsrCheckWritePolicy(_In_ ULONG MsrIndex);

/**
 * @function   Sr0MsrRead
 * @purpose    Read MSR value with rate limiting
 * @param      MsrIndex - MSR register index
 * @param      Value - Pointer to receive 64-bit value
 * @param      ProcessId - Caller process ID for rate limiting
 * @precondition IRQL <= DISPATCH_LEVEL
 * @returns    STATUS_SUCCESS, STATUS_QUOTA_EXCEEDED, or error
 * @thread-safety Thread-safe
 */
_IRQL_requires_max_(DISPATCH_LEVEL) NTSTATUS
    Sr0MsrRead(_In_ ULONG MsrIndex, _Out_ PULONG64 Value, _In_ ULONG ProcessId);

/**
 * @function   Sr0MsrWrite
 * @purpose    Write MSR value with policy check and rate limiting
 * @param      MsrIndex - MSR register index
 * @param      Value - 64-bit value to write
 * @param      ProcessId - Caller process ID for rate limiting
 * @precondition IRQL <= DISPATCH_LEVEL
 * @returns    STATUS_SUCCESS, STATUS_ACCESS_DENIED, STATUS_QUOTA_EXCEEDED
 * @thread-safety Thread-safe
 */
_IRQL_requires_max_(DISPATCH_LEVEL) NTSTATUS
    Sr0MsrWrite(_In_ ULONG MsrIndex, _In_ ULONG64 Value, _In_ ULONG ProcessId);

/**
 * @function   Sr0MsrIsNeverWritable
 * @purpose    Check if MSR is in never-writable list
 * @param      MsrIndex - MSR register index
 * @returns    TRUE if MSR is never writable, FALSE otherwise
 * @thread-safety Thread-safe (read-only check)
 */
_IRQL_requires_max_(DISPATCH_LEVEL) BOOLEAN
    Sr0MsrIsNeverWritable(_In_ ULONG MsrIndex);

/**
 * @function   Sr0MsrIsWhitelisted
 * @purpose    Check if MSR is in opt-in whitelist
 * @param      MsrIndex - MSR register index
 * @returns    TRUE if MSR is whitelisted, FALSE otherwise
 * @thread-safety Thread-safe (read-only check)
 */
_IRQL_requires_max_(DISPATCH_LEVEL) BOOLEAN
    Sr0MsrIsWhitelisted(_In_ ULONG MsrIndex);

#ifdef __cplusplus
}
#endif
