/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: ratelimit.c
 * Version: 1.0
 * Date: 2025-09-15
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - Per-Process Rate Limiting Implementation
 *   Sliding window algorithm with PID-based hash table.
 *
 * Security:
 *   - DoS prevention via per-process and global limits
 *   - Sliding window prevents burst attacks at window boundaries
 *   - Periodic cleanup prevents memory exhaustion
 *
 * Threading Model:
 *   - Hash table protected by KSPIN_LOCK
 *   - Safe at DISPATCH_LEVEL (required for IOCTL handlers)
 *   - Timer DPC for periodic cleanup
 */

#include "ratelimit.h"

#include <ntddk.h>

#include "..\..\include\safering0_public.h"

/*--------------------------------------------------------------------------*/
/* Global Rate Limit State                                                  */
/*--------------------------------------------------------------------------*/

static SR0_RATE_STATE g_RateState;
static BOOLEAN g_RateInitialized = FALSE;

/*--------------------------------------------------------------------------*/
/* Hash Function                                                            */
/*--------------------------------------------------------------------------*/

/*
 * Golden ratio hash for PID -> bucket mapping.
 * Provides good distribution across buckets.
 */
#define SR0_GOLDEN_RATIO 0x9E3779B9UL

__inline ULONG Sr0HashPid(_In_ ULONG ProcessId) {
  return (ProcessId * SR0_GOLDEN_RATIO) % SR0_RATE_HASH_BUCKETS;
}

/*--------------------------------------------------------------------------*/
/* Forward Declarations                                                     */
/*--------------------------------------------------------------------------*/

static KDEFERRED_ROUTINE Sr0RateLimitCleanupDpc;

_IRQL_requires_(DISPATCH_LEVEL) static PSR0_RATE_ENTRY
    Sr0FindOrCreateEntry(_In_ ULONG ProcessId, _In_ PLARGE_INTEGER Now);

_IRQL_requires_(DISPATCH_LEVEL) static ULONG
    Sr0CalculateEffectiveRate(_In_ PSR0_RATE_ENTRY Entry,
                              _In_ PLARGE_INTEGER Now);

/*--------------------------------------------------------------------------*/
/* Initialization / Shutdown                                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0RateLimitInitialize
 * @purpose    Initialize rate limiting subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition Hash table initialized, cleanup timer started
 * @returns    STATUS_SUCCESS (no allocations at init)
 * @thread-safety Single-threaded init only
 * @side-effects Starts cleanup timer
 */
_Use_decl_annotations_ NTSTATUS Sr0RateLimitInitialize(VOID) {
  ULONG i;
  LARGE_INTEGER dueTime;

  /* Initialize hash buckets */
  for (i = 0; i < SR0_RATE_HASH_BUCKETS; i++) {
    InitializeListHead(&g_RateState.HashBuckets[i]);
  }

  /* Initialize spinlock */
  KeInitializeSpinLock(&g_RateState.Lock);

  /* Set default limits */
  g_RateState.PerProcessLimit = SR0_RATE_DEFAULT_PER_PROCESS;
  g_RateState.GlobalLimit = SR0_RATE_DEFAULT_GLOBAL;
  g_RateState.GlobalCount = 0;
  g_RateState.GlobalPrevCount = 0;
  g_RateState.ShuttingDown = FALSE;
  g_RateState.EntryCount = 0;

  /* Initialize global window */
  KeQuerySystemTime(&g_RateState.GlobalWindowStart);

  /* Initialize cleanup timer and DPC */
  KeInitializeTimer(&g_RateState.CleanupTimer);
  KeInitializeDpc(&g_RateState.CleanupDpc, Sr0RateLimitCleanupDpc, NULL);

  /* Start cleanup timer (60 second interval) */
  dueTime.QuadPart = -((LONGLONG)SR0_RATE_CLEANUP_INTERVAL * 10000);
  KeSetTimerEx(&g_RateState.CleanupTimer, dueTime, SR0_RATE_CLEANUP_INTERVAL,
               &g_RateState.CleanupDpc);

  g_RateInitialized = TRUE;
  return STATUS_SUCCESS;
}

/**
 * @function   Sr0RateLimitShutdown
 * @purpose    Shutdown rate limiting subsystem
 * @precondition IRQL == PASSIVE_LEVEL
 * @postcondition All entries freed, timer cancelled
 * @thread-safety Single-threaded shutdown only
 * @side-effects Frees all rate limit entries
 */
_Use_decl_annotations_ VOID Sr0RateLimitShutdown(VOID) {
  KIRQL oldIrql;
  ULONG i;
  PLIST_ENTRY entry;
  PSR0_RATE_ENTRY rateEntry;

  if (!g_RateInitialized) {
    return;
  }

  /* Signal shutdown to prevent timer re-arm */
  InterlockedExchange(&g_RateState.ShuttingDown, TRUE);

  /* Cancel cleanup timer */
  KeCancelTimer(&g_RateState.CleanupTimer);

  /* Free all entries */
  KeAcquireSpinLock(&g_RateState.Lock, &oldIrql);

  for (i = 0; i < SR0_RATE_HASH_BUCKETS; i++) {
    while (!IsListEmpty(&g_RateState.HashBuckets[i])) {
      entry = RemoveHeadList(&g_RateState.HashBuckets[i]);
      rateEntry = CONTAINING_RECORD(entry, SR0_RATE_ENTRY, HashLink);
      ExFreePoolWithTag(rateEntry, SR0_POOL_TAG_RATE);
    }
  }

  g_RateState.EntryCount = 0;
  KeReleaseSpinLock(&g_RateState.Lock, oldIrql);
  g_RateInitialized = FALSE;
}

/*--------------------------------------------------------------------------*/
/* Rate Limit Check                                                         */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0RateLimitCheck
 * @purpose    Check if operation is within rate limits
 * @param      ProcessId - Caller process ID
 * @precondition g_RateInitialized == TRUE
 * @postcondition Counters updated if allowed
 * @returns    SR0_RATE_CHECK_RESULT
 * @thread-safety Thread-safe via spinlock
 * @side-effects Increments counters, may allocate entry
 */
_Use_decl_annotations_ SR0_RATE_CHECK_RESULT
Sr0RateLimitCheck(_In_ ULONG ProcessId) {
  KIRQL oldIrql;
  LARGE_INTEGER now;
  LONGLONG elapsedMs;
  ULONG effectiveGlobalRate;
  ULONG effectiveProcessRate;
  PSR0_RATE_ENTRY entry;
  SR0_RATE_CHECK_RESULT result = Sr0RateCheckAllowed;

  if (!g_RateInitialized) {
    return Sr0RateCheckAllowed; /* Fail open if not initialized */
  }

  KeQuerySystemTime(&now);
  KeAcquireSpinLock(&g_RateState.Lock, &oldIrql);

  /* Check global rate first */
  elapsedMs = (now.QuadPart - g_RateState.GlobalWindowStart.QuadPart) / 10000;

  if (elapsedMs >= SR0_RATE_WINDOW_MS) {
    /* Window expired - rotate */
    g_RateState.GlobalPrevCount = g_RateState.GlobalCount;
    g_RateState.GlobalCount = 0;
    g_RateState.GlobalWindowStart = now;
    elapsedMs = 0;
  }

  /* Calculate effective global rate using sliding window */
  effectiveGlobalRate =
      g_RateState.GlobalCount +
      (ULONG)(g_RateState.GlobalPrevCount * (SR0_RATE_WINDOW_MS - elapsedMs) /
              SR0_RATE_WINDOW_MS);

  if (effectiveGlobalRate >= g_RateState.GlobalLimit) {
    result = Sr0RateCheckExceededGlobal;
    goto Exit;
  }

  /* Find or create per-process entry */
  entry = Sr0FindOrCreateEntry(ProcessId, &now);
  if (entry == NULL) {
    /* Allocation failed - allow but don't track */
    InterlockedIncrement((volatile LONG*)&g_RateState.GlobalCount);
    goto Exit;
  }

  /* Calculate effective per-process rate */
  effectiveProcessRate = Sr0CalculateEffectiveRate(entry, &now);

  if (effectiveProcessRate >= g_RateState.PerProcessLimit) {
    result = Sr0RateCheckExceededProcess;
    goto Exit;
  }

  /* Within limits - increment counters */
  entry->CurrentWindowCount++;
  entry->LastActivity = now;
  InterlockedIncrement((volatile LONG*)&g_RateState.GlobalCount);

Exit:
  KeReleaseSpinLock(&g_RateState.Lock, oldIrql);
  return result;
}

/*--------------------------------------------------------------------------*/
/* Helper Functions                                                         */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0FindOrCreateEntry
 * @purpose    Find existing or create new rate entry for process
 * @param      ProcessId - Process ID to look up
 * @param      Now - Current timestamp
 * @precondition Lock held, IRQL == DISPATCH_LEVEL
 * @postcondition Entry returned (may be new allocation)
 * @returns    Rate entry or NULL on allocation failure
 * @thread-safety Caller holds lock
 * @side-effects May allocate new entry
 */
_Use_decl_annotations_ static PSR0_RATE_ENTRY Sr0FindOrCreateEntry(
    _In_ ULONG ProcessId, _In_ PLARGE_INTEGER Now) {
  ULONG bucket = Sr0HashPid(ProcessId);
  PLIST_ENTRY listEntry;
  PSR0_RATE_ENTRY entry;

  /* Search bucket for existing entry */
  for (listEntry = g_RateState.HashBuckets[bucket].Flink;
       listEntry != &g_RateState.HashBuckets[bucket];
       listEntry = listEntry->Flink) {
    entry = CONTAINING_RECORD(listEntry, SR0_RATE_ENTRY, HashLink);
    if (entry->ProcessId == ProcessId) {
      return entry;
    }
  }

  /* Not found - check entry limit before allocating */
  if (g_RateState.EntryCount >= SR0_RATE_MAX_ENTRIES) {
    return NULL; /* Limit reached - DoS prevention */
  }

  /* Allocate new entry */
  entry = (PSR0_RATE_ENTRY)ExAllocatePool2(
      POOL_FLAG_NON_PAGED, sizeof(SR0_RATE_ENTRY), SR0_POOL_TAG_RATE);
  if (entry == NULL) {
    return NULL;
  }

  /* Track entry count */
  g_RateState.EntryCount++;

  /* Initialize new entry */
  entry->ProcessId = ProcessId;
  entry->CurrentWindowCount = 0;
  entry->PrevWindowCount = 0;
  entry->WindowStart = *Now;
  entry->LastActivity = *Now;

  /* Insert into bucket */
  InsertHeadList(&g_RateState.HashBuckets[bucket], &entry->HashLink);

  return entry;
}

/**
 * @function   Sr0CalculateEffectiveRate
 * @purpose    Calculate effective rate using sliding window
 * @param      Entry - Rate entry for process
 * @param      Now - Current timestamp
 * @precondition Lock held
 * @postcondition Entry window may be rotated
 * @returns    Effective operations per window
 * @thread-safety Caller holds lock
 * @side-effects May rotate window
 */
_Use_decl_annotations_ static ULONG Sr0CalculateEffectiveRate(
    _In_ PSR0_RATE_ENTRY Entry, _In_ PLARGE_INTEGER Now) {
  LONGLONG elapsedMs;

  elapsedMs = (Now->QuadPart - Entry->WindowStart.QuadPart) / 10000;

  if (elapsedMs >= SR0_RATE_WINDOW_MS) {
    /* Window expired - rotate */
    Entry->PrevWindowCount = Entry->CurrentWindowCount;
    Entry->CurrentWindowCount = 0;
    Entry->WindowStart = *Now;
    elapsedMs = 0;
  }

  /* Sliding window: weighted sum of current and previous */
  return Entry->CurrentWindowCount +
         (ULONG)(Entry->PrevWindowCount * (SR0_RATE_WINDOW_MS - elapsedMs) /
                 SR0_RATE_WINDOW_MS);
}

/*--------------------------------------------------------------------------*/
/* Cleanup DPC                                                              */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0RateLimitCleanupDpc
 * @purpose    Periodic cleanup of stale rate entries
 * @param      Dpc - DPC object
 * @param      DeferredContext - Unused
 * @param      SystemArgument1 - Unused
 * @param      SystemArgument2 - Unused
 * @precondition IRQL == DISPATCH_LEVEL
 * @postcondition Stale entries removed
 * @thread-safety Acquires spinlock
 * @side-effects Frees memory
 */
_Use_decl_annotations_ static VOID Sr0RateLimitCleanupDpc(
    _In_ PKDPC Dpc, _In_opt_ PVOID DeferredContext,
    _In_opt_ PVOID SystemArgument1, _In_opt_ PVOID SystemArgument2) {
  KIRQL oldIrql;
  LARGE_INTEGER now;
  ULONG i;
  PLIST_ENTRY entry;
  PLIST_ENTRY next;
  PSR0_RATE_ENTRY rateEntry;
  LONGLONG idleMs;

  UNREFERENCED_PARAMETER(Dpc);
  UNREFERENCED_PARAMETER(DeferredContext);
  UNREFERENCED_PARAMETER(SystemArgument1);
  UNREFERENCED_PARAMETER(SystemArgument2);

  if (g_RateState.ShuttingDown) {
    return;
  }

  KeQuerySystemTime(&now);
  KeAcquireSpinLock(&g_RateState.Lock, &oldIrql);

  /* Scan all buckets for stale entries */
  for (i = 0; i < SR0_RATE_HASH_BUCKETS; i++) {
    for (entry = g_RateState.HashBuckets[i].Flink;
         entry != &g_RateState.HashBuckets[i]; entry = next) {
      next = entry->Flink;
      rateEntry = CONTAINING_RECORD(entry, SR0_RATE_ENTRY, HashLink);

      idleMs = (now.QuadPart - rateEntry->LastActivity.QuadPart) / 10000;
      if (idleMs > SR0_RATE_STALE_THRESHOLD) {
        RemoveEntryList(entry);
        ExFreePoolWithTag(rateEntry, SR0_POOL_TAG_RATE);
        g_RateState.EntryCount--;
      }
    }
  }

  KeReleaseSpinLock(&g_RateState.Lock, oldIrql);

  /* Note: KeSetTimerEx with period auto-repeats, no manual re-arm needed */
}

/*--------------------------------------------------------------------------*/
/* Statistics                                                               */
/*--------------------------------------------------------------------------*/

/**
 * @function   Sr0RateLimitGetStats
 * @purpose    Get current rate limit statistics
 * @param      GlobalCount - Output for current global count
 * @param      PerProcessLimit - Output for per-process limit
 * @param      GlobalLimit - Output for global limit
 * @precondition None
 * @postcondition Output parameters filled
 * @returns    void
 * @thread-safety Thread-safe (reads volatile variables)
 * @side-effects None
 */
_Use_decl_annotations_ VOID Sr0RateLimitGetStats(
    _Out_opt_ PULONG GlobalCount, _Out_opt_ PULONG PerProcessLimit,
    _Out_opt_ PULONG GlobalLimit) {
  if (GlobalCount != NULL) {
    *GlobalCount = g_RateState.GlobalCount;
  }
  if (PerProcessLimit != NULL) {
    *PerProcessLimit = g_RateState.PerProcessLimit;
  }
  if (GlobalLimit != NULL) {
    *GlobalLimit = g_RateState.GlobalLimit;
  }
}
