/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: msr.c
 * Version: 1.0
 * Date: 2025-11-15
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - MSR Access with Tiered Security Policy
 *   Implements secure MSR read/write with three-tier policy:
 *   1. Never-writable: Syscall/interrupt MSRs - ALWAYS blocked
 *   2. Opt-in whitelist: Performance/power MSRs - requires registry opt-in
 *   3. Default: All reads allowed, all writes denied
 *
 * Platform Support:
 *   - Intel: Performance counters, HWP, RAPL power limits, turbo ratio
 *   - AMD: Performance counters, P-state definitions (undervolting)
 *
 * Security:
 *   - Prevents kernel code execution via MSR_IA32_LSTAR modification
 *   - Blocks NX/SMEP bypass via MSR_IA32_EFER
 *   - Rate limiting prevents DoS
 *   - ETW logging for forensics
 *
 * Threading Model:
 *   - Policy checks are read-only, thread-safe
 *   - MSR intrinsics are inherently per-CPU
 */

#include "msr.h"

#include <intrin.h>
#include <ntddk.h>

#include "ratelimit.h"
#include "safering0_main.h"

/*--------------------------------------------------------------------------*/
/* Never-Writable MSR List                                                  */
/*--------------------------------------------------------------------------*/

/*
 * Static array of MSRs that are NEVER writable regardless of opt-in.
 * These control syscall entry points and critical CPU features.
 * Writing to any of these could enable kernel code execution.
 */
static const ULONG g_NeverWritableMsrs[] = {
    /* Syscall entry points - CRITICAL attack surface */
    MSR_IA32_LSTAR,  /* 0xC0000082 - Long mode syscall entry */
    MSR_IA32_CSTAR,  /* 0xC0000083 - Compat mode syscall */
    MSR_IA32_STAR,   /* 0xC0000081 - Syscall segment selectors */
    MSR_IA32_SFMASK, /* 0xC0000084 - Syscall flag mask */

    /* Legacy SYSENTER */
    MSR_IA32_SYSENTER_CS,  /* 0x174 */
    MSR_IA32_SYSENTER_ESP, /* 0x175 */
    MSR_IA32_SYSENTER_EIP, /* 0x176 */

    /* Extended Feature Enable Register */
    MSR_IA32_EFER, /* 0xC0000080 - Long mode, NX enable */

    /* Debug/Trace control */
    MSR_IA32_DEBUGCTL, /* 0x1D9 */

    /* Page Attribute Table */
    MSR_IA32_PAT, /* 0x277 */

    /* Miscellaneous Enable */
    MSR_IA32_MISC_ENABLE, /* 0x1A0 */

    /* AMD-specific */
    MSR_AMD_SYSCFG,      /* 0xC0010010 */
    MSR_AMD_VM_HSAVE_PA, /* 0xC0010117 */

    /* Intel VMX basic */
    MSR_IA32_VMX_BASIC /* 0x480 */
};

#define NEVER_WRITABLE_COUNT \
  (sizeof(g_NeverWritableMsrs) / sizeof(g_NeverWritableMsrs[0]))

/*--------------------------------------------------------------------------*/
/* Opt-In Whitelist                                                         */
/*--------------------------------------------------------------------------*/

/*
 * MSRs that can be written when registry opt-in is enabled.
 * These are for performance monitoring and power management.
 * Supports both Intel and AMD platforms.
 */
static const ULONG g_WhitelistMsrs[] = {
    /*======================================================================*/
    /* Intel Performance MSRs                                               */
    /*======================================================================*/

    /* Performance Event Select */
    MSR_IA32_PERFEVTSEL0, /* 0x186 */
    MSR_IA32_PERFEVTSEL1, /* 0x187 */
    MSR_IA32_PERFEVTSEL2, /* 0x188 */
    MSR_IA32_PERFEVTSEL3, /* 0x189 */

    /* Performance Counters */
    MSR_IA32_PMC0, /* 0xC1 */
    MSR_IA32_PMC1, /* 0xC2 */
    MSR_IA32_PMC2, /* 0xC3 */
    MSR_IA32_PMC3, /* 0xC4 */

    /* Fixed-Function Counters */
    MSR_IA32_FIXED_CTR0,     /* 0x309 */
    MSR_IA32_FIXED_CTR1,     /* 0x30A */
    MSR_IA32_FIXED_CTR2,     /* 0x30B */
    MSR_IA32_FIXED_CTR_CTRL, /* 0x38D */

    /* Performance/Frequency Control */
    MSR_IA32_PERF_CTL, /* 0x199 */

    /* Energy/Power Management */
    MSR_IA32_ENERGY_PERF_BIAS, /* 0x1B0 */
    MSR_PKG_POWER_LIMIT,       /* 0x610 */
    MSR_PP0_POWER_LIMIT,       /* 0x638 */
    MSR_PP1_POWER_LIMIT,       /* 0x640 */

    /* Turbo Ratio */
    MSR_TURBO_RATIO_LIMIT, /* 0x1AD */

    /* HWP Request */
    MSR_IA32_HWP_REQUEST, /* 0x774 */

    /*======================================================================*/
    /* AMD Performance MSRs                                                 */
    /*======================================================================*/

    /* AMD Performance Event Select */
    MSR_AMD_PERFEVTSEL0, /* 0xC0010000 */
    MSR_AMD_PERFEVTSEL1, /* 0xC0010001 */
    MSR_AMD_PERFEVTSEL2, /* 0xC0010002 */
    MSR_AMD_PERFEVTSEL3, /* 0xC0010003 */

    /* AMD Performance Counters */
    MSR_AMD_PERFCTR0, /* 0xC0010004 */
    MSR_AMD_PERFCTR1, /* 0xC0010005 */
    MSR_AMD_PERFCTR2, /* 0xC0010006 */
    MSR_AMD_PERFCTR3, /* 0xC0010007 */

    /*======================================================================*/
    /* AMD P-State / Voltage MSRs (Undervolting Support)                    */
    /*======================================================================*/

    /* P-State Control */
    MSR_AMD_PSTATE_CTL, /* 0xC0010062 */

    /* P-State Definitions (voltage/frequency per state) */
    MSR_AMD_PSTATE_DEF_0, /* 0xC0010064 */
    MSR_AMD_PSTATE_DEF_1, /* 0xC0010065 */
    MSR_AMD_PSTATE_DEF_2, /* 0xC0010066 */
    MSR_AMD_PSTATE_DEF_3, /* 0xC0010067 */
    MSR_AMD_PSTATE_DEF_4, /* 0xC0010068 */
    MSR_AMD_PSTATE_DEF_5, /* 0xC0010069 */
    MSR_AMD_PSTATE_DEF_6, /* 0xC001006A */
    MSR_AMD_PSTATE_DEF_7  /* 0xC001006B */
};

#define WHITELIST_COUNT (sizeof(g_WhitelistMsrs) / sizeof(g_WhitelistMsrs[0]))

/*--------------------------------------------------------------------------*/
/* Initialization / Shutdown                                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0MsrInitialize
 * @purpose    Initialize MSR policy subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition MSR subsystem ready for use
 * @returns    STATUS_SUCCESS (no allocations needed)
 * @thread-safety Single-threaded init only
 * @side-effects None
 */
_Use_decl_annotations_ NTSTATUS Sr0MsrInitialize(VOID) {
  /* No dynamic allocations needed - policy is static */
  return STATUS_SUCCESS;
}

/**
 * @function   Sr0MsrShutdown
 * @purpose    Shutdown MSR policy subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition MSR subsystem shut down
 * @thread-safety Single-threaded shutdown only
 * @side-effects None
 */
_Use_decl_annotations_ VOID Sr0MsrShutdown(VOID) {
  /* No cleanup needed - policy is static */
}

/*--------------------------------------------------------------------------*/
/* Policy Check Functions                                                   */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0MsrIsNeverWritable
 * @purpose    Check if MSR is in never-writable list
 * @param      MsrIndex - MSR register index to check
 * @precondition None
 * @postcondition None (read-only check)
 * @returns    TRUE if MSR is never writable, FALSE otherwise
 * @thread-safety Thread-safe (read-only static data)
 * @side-effects None
 */
_Use_decl_annotations_ BOOLEAN Sr0MsrIsNeverWritable(_In_ ULONG MsrIndex) {
  ULONG i;

  /* Check against static never-writable list */
  for (i = 0; i < NEVER_WRITABLE_COUNT; i++) {
    if (g_NeverWritableMsrs[i] == MsrIndex) {
      return TRUE;
    }
  }

  /* Also block entire VMX MSR range (0x480-0x48F) */
  if (MsrIndex >= MSR_IA32_VMX_RANGE_START &&
      MsrIndex <= MSR_IA32_VMX_RANGE_END) {
    return TRUE;
  }

  return FALSE;
}

/**
 * @function   Sr0MsrIsWhitelisted
 * @purpose    Check if MSR is in opt-in whitelist
 * @param      MsrIndex - MSR register index to check
 * @precondition None
 * @postcondition None (read-only check)
 * @returns    TRUE if MSR is whitelisted, FALSE otherwise
 * @thread-safety Thread-safe (read-only static data)
 * @side-effects None
 */
_Use_decl_annotations_ BOOLEAN Sr0MsrIsWhitelisted(_In_ ULONG MsrIndex) {
  ULONG i;

  for (i = 0; i < WHITELIST_COUNT; i++) {
    if (g_WhitelistMsrs[i] == MsrIndex) {
      return TRUE;
    }
  }

  return FALSE;
}

/**
 * @function   Sr0MsrCheckWritePolicy
 * @purpose    Check if MSR write is allowed by tiered policy
 * @param      MsrIndex - MSR register index to check
 * @precondition g_Sr0Context != NULL
 * @postcondition None (read-only check)
 * @returns    SR0_MSR_CHECK_RESULT indicating allow/deny reason
 * @thread-safety Thread-safe
 * @side-effects None
 *
 * Policy evaluation order:
 * 1. Check never-writable list -> DENY if match (highest priority)
 * 2. Check opt-in registry flag -> DENY if disabled
 * 3. Check whitelist -> ALLOW if match, DENY otherwise
 */
_Use_decl_annotations_ SR0_MSR_CHECK_RESULT
Sr0MsrCheckWritePolicy(_In_ ULONG MsrIndex) {
  /*
   * Tier 1: Never-writable MSRs - ALWAYS blocked
   * These are syscall/interrupt handlers and critical CPU features.
   */
  if (Sr0MsrIsNeverWritable(MsrIndex)) {
    return Sr0MsrCheckDeniedNeverWrite;
  }

  /*
   * Tier 2: Check if opt-in is enabled
   * Default mode (opt-in disabled) denies ALL writes.
   */
  if (!g_Sr0Context->MsrWriteOptIn) {
    return Sr0MsrCheckDeniedNoOptIn;
  }

  /*
   * Tier 3: Opt-in enabled - check whitelist
   * Only whitelisted MSRs are allowed when opt-in is enabled.
   */
  if (Sr0MsrIsWhitelisted(MsrIndex)) {
    return Sr0MsrCheckAllowed;
  }

  return Sr0MsrCheckDeniedNotWhitelist;
}

/**
 * @function   Sr0MsrGetBlockReason
 * @purpose    Convert policy result to human-readable string for ETW
 * @param      Result - Policy check result
 * @returns    Static string describing denial reason
 * @thread-safety Thread-safe (returns static strings)
 */
static PCSTR Sr0MsrGetBlockReason(_In_ SR0_MSR_CHECK_RESULT Result) {
  switch (Result) {
    case Sr0MsrCheckDeniedNeverWrite:
      return "Security-critical MSR (never writable)";
    case Sr0MsrCheckDeniedNoOptIn:
      return "MSR writes disabled (no registry opt-in)";
    case Sr0MsrCheckDeniedNotWhitelist:
      return "MSR not in allowed whitelist";
    default:
      return "Unknown denial reason";
  }
}

/*--------------------------------------------------------------------------*/
/* MSR Read/Write Operations                                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0MsrRead
 * @purpose    Read MSR value with logging
 * @param      MsrIndex - MSR register index
 * @param      Value - Pointer to receive 64-bit value
 * @param      ProcessId - Caller process ID for logging/rate limiting
 * @precondition g_Sr0Context != NULL, Value != NULL
 * @postcondition *Value contains MSR value on success
 * @returns    STATUS_SUCCESS, STATUS_QUOTA_EXCEEDED, or
 * STATUS_INVALID_PARAMETER
 * @thread-safety Thread-safe
 * @side-effects Reads hardware MSR, logs via ETW
 *
 * Note: MSR reads are generally safe and always allowed.
 * Rate limiting may be applied to prevent DoS.
 * Invalid MSR indices are caught via SEH and return STATUS_INVALID_PARAMETER.
 */
_Use_decl_annotations_ NTSTATUS Sr0MsrRead(_In_ ULONG MsrIndex,
                                           _Out_ PULONG64 Value,
                                           _In_ ULONG ProcessId) {
  SR0_RATE_CHECK_RESULT rateResult;

  /* Check rate limit before operation */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    return STATUS_QUOTA_EXCEEDED;
  }

  /*
   * Perform the MSR read with SEH protection.
   * Invalid MSR indices cause #GP(0) which we catch here to prevent BSOD.
   * x64 uses table-based SEH - no runtime overhead for normal path.
   */
  __try {
    *Value = __readmsr(MsrIndex);
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    /* Invalid MSR index - return error instead of BSOD */
    return STATUS_INVALID_PARAMETER;
  }

  /* Log successful read */
  Sr0EtwLogMsrRead(ProcessId, MsrIndex, *Value);

  return STATUS_SUCCESS;
}

/**
 * @function   Sr0MsrWrite
 * @purpose    Write MSR value with policy check
 * @param      MsrIndex - MSR register index
 * @param      Value - 64-bit value to write
 * @param      ProcessId - Caller process ID for logging/rate limiting
 * @precondition g_Sr0Context != NULL
 * @postcondition MSR written on success, unchanged on denial
 * @returns    STATUS_SUCCESS, STATUS_ACCESS_DENIED, STATUS_QUOTA_EXCEEDED,
 *             or STATUS_INVALID_PARAMETER (invalid MSR index)
 * @thread-safety Thread-safe
 * @side-effects Writes hardware MSR on success, logs via ETW
 */
_Use_decl_annotations_ NTSTATUS Sr0MsrWrite(_In_ ULONG MsrIndex,
                                            _In_ ULONG64 Value,
                                            _In_ ULONG ProcessId) {
  SR0_MSR_CHECK_RESULT policyResult;
  SR0_RATE_CHECK_RESULT rateResult;

  /* Check rate limit before operation */
  rateResult = Sr0RateLimitCheck(ProcessId);
  if (rateResult != Sr0RateCheckAllowed) {
    Sr0EtwLogRateExceeded(ProcessId, rateResult == Sr0RateCheckExceededGlobal);
    return STATUS_QUOTA_EXCEEDED;
  }

  /* Check write policy */
  policyResult = Sr0MsrCheckWritePolicy(MsrIndex);

  if (policyResult != Sr0MsrCheckAllowed) {
    /* Log blocked write with denial reason */
    Sr0EtwLogMsrBlocked(ProcessId, MsrIndex, Value,
                        Sr0MsrGetBlockReason(policyResult));
    return STATUS_ACCESS_DENIED;
  }

  /*
   * Policy allows - perform the write with SEH protection.
   * Invalid MSR indices cause #GP(0) which we catch here to prevent BSOD.
   */
  __try {
    __writemsr(MsrIndex, Value);
  } __except (EXCEPTION_EXECUTE_HANDLER) {
    /* Invalid MSR index - return error instead of BSOD */
    return STATUS_INVALID_PARAMETER;
  }

  /* Log successful write (security-noteworthy operation) */
  Sr0EtwLogMsrWrite(ProcessId, MsrIndex, Value);

  return STATUS_SUCCESS;
}
