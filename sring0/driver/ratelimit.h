/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: ratelimit.h
 * Version: 1.0
 * Date: 2025-09-14
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - Per-Process Rate Limiting
 *   Implements sliding window rate limiting to prevent DoS attacks.
 *   Tracks operations per process using a PID-based hash table.
 *
 * Security:
 *   - Prevents flooding attacks via per-process limits
 *   - Global limit prevents coordinated multi-process attacks
 *   - Sliding window provides smooth rate enforcement
 *
 * Threading Model:
 *   - Hash table protected by KSPIN_LOCK (DISPATCH_LEVEL safe)
 *   - Counters updated via interlocked operations
 *   - Timer-based cleanup of stale entries
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
/* Rate Limit Configuration                                                 */
/*--------------------------------------------------------------------------*/

#define SR0_RATE_HASH_BUCKETS 256       /* Hash table size */
#define SR0_RATE_WINDOW_MS 1000         /* Sliding window in milliseconds */
#define SR0_RATE_CLEANUP_INTERVAL 60000 /* Cleanup stale entries every 60s */
#define SR0_RATE_STALE_THRESHOLD 300000 /* Remove entries idle > 5 minutes */
#define SR0_RATE_MAX_ENTRIES 4096 /* Max tracked processes (DoS prevention) */

/* Default limits (can be adjusted via registry in future) */
#define SR0_RATE_DEFAULT_PER_PROCESS 100 /* ops/sec per process */
#define SR0_RATE_DEFAULT_GLOBAL 1000     /* ops/sec global */

/*--------------------------------------------------------------------------*/
/* Rate Limit Result                                                        */
/*--------------------------------------------------------------------------*/

typedef enum _SR0_RATE_CHECK_RESULT {
  Sr0RateCheckAllowed = 0,     /* Operation permitted */
  Sr0RateCheckExceededProcess, /* Per-process limit exceeded */
  Sr0RateCheckExceededGlobal   /* Global limit exceeded */
} SR0_RATE_CHECK_RESULT;

/*--------------------------------------------------------------------------*/
/* Rate Limit Entry (Per-Process)                                           */
/*--------------------------------------------------------------------------*/

/*
 * Each entry tracks rate for a single process using sliding window.
 * Uses golden ratio hash for even distribution.
 */
typedef struct _SR0_RATE_ENTRY {
  LIST_ENTRY HashLink;        /* Link in hash bucket */
  ULONG ProcessId;            /* Process identifier */
  ULONG CurrentWindowCount;   /* Ops in current window */
  ULONG PrevWindowCount;      /* Ops in previous window */
  LARGE_INTEGER WindowStart;  /* Current window start time */
  LARGE_INTEGER LastActivity; /* Last operation timestamp */
} SR0_RATE_ENTRY, *PSR0_RATE_ENTRY;

/*--------------------------------------------------------------------------*/
/* Rate Limit State                                                         */
/*--------------------------------------------------------------------------*/

typedef struct _SR0_RATE_STATE {
  LIST_ENTRY HashBuckets[SR0_RATE_HASH_BUCKETS]; /* PID hash table */
  KSPIN_LOCK Lock;                               /* Protects hash table */

  volatile ULONG PerProcessLimit; /* Current per-process limit */
  volatile ULONG GlobalLimit;     /* Current global limit */

  volatile ULONG GlobalCount;      /* Ops in current global window */
  ULONG GlobalPrevCount;           /* Ops in previous global window */
  LARGE_INTEGER GlobalWindowStart; /* Global window start time */

  KTIMER CleanupTimer;        /* Periodic cleanup timer */
  KDPC CleanupDpc;            /* DPC for cleanup */
  volatile LONG ShuttingDown; /* Cleanup disabled during shutdown */
  volatile ULONG EntryCount;  /* Current number of tracked entries */
} SR0_RATE_STATE, *PSR0_RATE_STATE;

/*--------------------------------------------------------------------------*/
/* Rate Limit Functions                                                     */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0RateLimitInitialize
 * @purpose    Initialize rate limiting subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 * @returns    STATUS_SUCCESS or STATUS_INSUFFICIENT_RESOURCES
 */
_IRQL_requires_(PASSIVE_LEVEL) NTSTATUS Sr0RateLimitInitialize(VOID);

/**
 * @function   Sr0RateLimitShutdown
 * @purpose    Shutdown rate limiting subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 */
_IRQL_requires_(PASSIVE_LEVEL) VOID Sr0RateLimitShutdown(VOID);

/**
 * @function   Sr0RateLimitCheck
 * @purpose    Check if operation is within rate limits
 * @param      ProcessId - Caller process ID
 * @precondition IRQL <= DISPATCH_LEVEL
 * @returns    SR0_RATE_CHECK_RESULT indicating if allowed
 * @thread-safety Thread-safe via spinlock
 *
 * This function:
 * 1. Checks global limit first
 * 2. Looks up or creates per-process entry
 * 3. Uses sliding window for smooth rate calculation
 * 4. Increments counters if allowed
 */
_IRQL_requires_max_(DISPATCH_LEVEL) SR0_RATE_CHECK_RESULT
    Sr0RateLimitCheck(_In_ ULONG ProcessId);

/**
 * @function   Sr0RateLimitGetStats
 * @purpose    Get current rate limit statistics
 * @param      GlobalCount - Current global ops count
 * @param      PerProcessLimit - Current per-process limit
 * @param      GlobalLimit - Current global limit
 * @precondition IRQL <= DISPATCH_LEVEL
 */
_IRQL_requires_max_(DISPATCH_LEVEL) VOID
    Sr0RateLimitGetStats(_Out_opt_ PULONG GlobalCount,
                         _Out_opt_ PULONG PerProcessLimit,
                         _Out_opt_ PULONG GlobalLimit);

#ifdef __cplusplus
}
#endif
