#pragma once

/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: safering0_version.h
 * Version: 1.0
 * Date: 2025-12-04
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 * SafeRing0 - Version Information
 *
 * Version macros for SafeRing0 driver and DLL. Provides both SafeRing0
 * native versioning and WinRing0 compatibility version numbers for
 * drop-in replacement scenarios.
 *
 * Security: N/A - version information only
 */

#ifdef __cplusplus
extern "C" {
#endif

/*--------------------------------------------------------------------------*/
/* SafeRing0 Native Version Numbers                                         */
/*--------------------------------------------------------------------------*/

#define SR0_VERSION_MAJOR 1
#define SR0_VERSION_MINOR 0
#define SR0_VERSION_REVISION 0
#define SR0_VERSION_BUILD 1

/*
 * Combined version for registry/comparison
 * Format: 0xMMmmRRBB (Major.Minor.Revision.Build)
 */
#define SR0_VERSION_COMBINED                                   \
  (((SR0_VERSION_MAJOR) << 24) | ((SR0_VERSION_MINOR) << 16) | \
   ((SR0_VERSION_REVISION) << 8) | (SR0_VERSION_BUILD))

/*--------------------------------------------------------------------------*/
/* Version Strings                                                          */
/*--------------------------------------------------------------------------*/

#define SR0_VERSION_STRING "1.0.0.1"
#define SR0_VERSION_STRING_W L"1.0.0.1"

#define SR0_PRODUCT_NAME "SafeRing0"
#define SR0_PRODUCT_NAME_W L"SafeRing0"

#define SR0_COMPANY_NAME "ziX Performance Labs"
#define SR0_COMPANY_NAME_W L"ziX Performance Labs"

#define SR0_COPYRIGHT "(c) 2025 ziX Performance Labs"
#define SR0_COPYRIGHT_W L"(c) 2025 ziX Performance Labs"

/*--------------------------------------------------------------------------*/
/* WinRing0 Compatibility Version                                           */
/*--------------------------------------------------------------------------*/

/*
 * Report WinRing0 1.2.0.5 for drop-in compatibility.
 * Apps checking driver version will see familiar values.
 * Reference: OLS_DRIVER_RELESE = 5 in original WinRing0
 */
#define SR0_COMPAT_VERSION_MAJOR 1
#define SR0_COMPAT_VERSION_MINOR 2
#define SR0_COMPAT_VERSION_REVISION 0
#define SR0_COMPAT_VERSION_RELEASE 5

/*
 * Packed compatibility version for IOCTL_OLS_GET_DRIVER_VERSION
 * Format matches WinRing0: (Major << 24) | (Minor << 16) | (Rev << 8) | Release
 * This evaluates to 0x01020005 = 1.2.0.5
 */
#define SR0_COMPAT_VERSION_PACKED                                            \
  (((SR0_COMPAT_VERSION_MAJOR) << 24) | ((SR0_COMPAT_VERSION_MINOR) << 16) | \
   ((SR0_COMPAT_VERSION_REVISION) << 8) | (SR0_COMPAT_VERSION_RELEASE))

#ifdef __cplusplus
}
#endif
