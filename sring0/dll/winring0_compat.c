/*
 * Author: Colin MacRitchie
 * Organization: ziX Performance Labs
 * File: winring0_compat.c
 * Version: 1.0
 * Date: 2025-11-06
 * Copyright:
 *   (c) 2025 ziX Performance Labs.
 *
 * Summary:
 *   SafeRing0 - WinRing0-Compatible User-Mode DLL
 *   Provides drop-in compatible API for applications using WinRing0.
 *   All functions wrap DeviceIoControl calls to the SafeRing0 driver.
 *
 * Compatibility:
 *   - HWiNFO64, AIDA64, Open Hardware Monitor, LibreHardwareMonitor
 *   - ThrottleStop, Intel XTU, Ryzen Master
 *   - Custom applications using WinRing0 API
 *
 * Security:
 *   - Requires Administrator privileges (driver ACL enforced)
 *   - MSR writes subject to driver-side whitelist policy
 *   - Physical memory access blocked for kernel address ranges
 */

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <windows.h>
#include <winioctl.h>

/* Disable signed/unsigned mismatch for DeviceIoControl size params */
#pragma warning(disable : 4245)

/* Include IOCTL definitions from driver */
#include "safering0_ioctl.h"
#include "safering0_version.h"

/*--------------------------------------------------------------------------*/
/* DLL Status Codes (WinRing0 compatible)                                   */
/*--------------------------------------------------------------------------*/

#define OLS_DLL_NO_ERROR 0
#define OLS_DLL_UNKNOWN_ERROR 1
#define OLS_DLL_DRIVER_NOT_LOADED 2
#define OLS_DLL_DRIVER_NOT_FOUND 3
#define OLS_DLL_DRIVER_UNLOADED 4
#define OLS_DLL_DRIVER_NOT_LOADED_ON_NETWORK 5
#define OLS_DLL_UNSUPPORTED_PLATFORM 6
#define OLS_DLL_DRIVER_NOT_LOADED_VISTA_UAC 7

/*--------------------------------------------------------------------------*/
/* Global State                                                             */
/*--------------------------------------------------------------------------*/

static HANDLE g_hDevice = INVALID_HANDLE_VALUE;
static DWORD g_DllStatus = OLS_DLL_DRIVER_NOT_LOADED;
static volatile LONG g_RefCount = 0;

/* Device symbolic link name (WinRing0 compatible) */
static const WCHAR g_DeviceName[] = L"\\\\.\\SafeRing0";
static const WCHAR g_DeviceNameCompat[] = L"\\\\.\\WinRing0_1_2_0";

/*--------------------------------------------------------------------------*/
/* Forward Declarations                                                     */
/*--------------------------------------------------------------------------*/

/* PCI functions forward-declared for use by Byte/Word variants */
__declspec(dllexport) DWORD WINAPI ReadPciConfigDword(DWORD pciAddress,
                                                      BYTE regAddress);
__declspec(dllexport) void WINAPI WritePciConfigDword(DWORD pciAddress,
                                                      BYTE regAddress,
                                                      DWORD value);

/*--------------------------------------------------------------------------*/
/* Internal Helpers                                                         */
/*--------------------------------------------------------------------------*/

/**
 * @function   OpenDriver
 * @purpose    Open handle to SafeRing0 driver device
 * @returns    TRUE on success, FALSE on failure (sets g_DllStatus)
 */
static BOOL OpenDriver(void) {
  /* Try SafeRing0 name first */
  g_hDevice = CreateFileW(g_DeviceName, GENERIC_READ | GENERIC_WRITE,
                          FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                          OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    /* Fall back to WinRing0 compat name */
    g_hDevice = CreateFileW(g_DeviceNameCompat, GENERIC_READ | GENERIC_WRITE,
                            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
  }

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    DWORD error = GetLastError();
    if (error == ERROR_ACCESS_DENIED) {
      g_DllStatus = OLS_DLL_DRIVER_NOT_LOADED_VISTA_UAC;
    } else if (error == ERROR_FILE_NOT_FOUND) {
      g_DllStatus = OLS_DLL_DRIVER_NOT_FOUND;
    } else {
      g_DllStatus = OLS_DLL_UNKNOWN_ERROR;
    }
    return FALSE;
  }

  g_DllStatus = OLS_DLL_NO_ERROR;
  return TRUE;
}

/**
 * @function   CloseDriver
 * @purpose    Close handle to driver device
 */
static void CloseDriver(void) {
  if (g_hDevice != INVALID_HANDLE_VALUE) {
    CloseHandle(g_hDevice);
    g_hDevice = INVALID_HANDLE_VALUE;
  }
  g_DllStatus = OLS_DLL_DRIVER_NOT_LOADED;
}

/*--------------------------------------------------------------------------*/
/* DLL Entry Point                                                          */
/*--------------------------------------------------------------------------*/

BOOL WINAPI DllMain(HINSTANCE hInstDll, DWORD dwReason, LPVOID lpReserved) {
  (void)hInstDll;
  (void)lpReserved;

  switch (dwReason) {
    case DLL_PROCESS_ATTACH:
      DisableThreadLibraryCalls(hInstDll);
      break;
    case DLL_PROCESS_DETACH:
      if (g_hDevice != INVALID_HANDLE_VALUE) {
        CloseDriver();
      }
      break;
  }
  return TRUE;
}

/*--------------------------------------------------------------------------*/
/* Initialization / Shutdown                                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   InitializeOls
 * @purpose    Initialize connection to SafeRing0 driver
 * @returns    TRUE on success, FALSE on failure
 */
__declspec(dllexport) BOOL WINAPI InitializeOls(void) {
  if (InterlockedIncrement(&g_RefCount) == 1) {
    if (!OpenDriver()) {
      InterlockedDecrement(&g_RefCount);
      return FALSE;
    }
  }
  return TRUE;
}

/**
 * @function   DeinitializeOls
 * @purpose    Close connection to SafeRing0 driver
 */
__declspec(dllexport) void WINAPI DeinitializeOls(void) {
  if (InterlockedDecrement(&g_RefCount) == 0) {
    CloseDriver();
  }
}

/*--------------------------------------------------------------------------*/
/* Status / Version Functions                                               */
/*--------------------------------------------------------------------------*/

/**
 * @function   GetDllStatus
 * @purpose    Get current DLL/driver status
 * @returns    OLS_DLL_* status code
 */
__declspec(dllexport) DWORD WINAPI GetDllStatus(void) { return g_DllStatus; }

/**
 * @function   GetDriverVersion
 * @purpose    Get driver version (WinRing0 format: 0xMMmmRRBB)
 * @returns    Version or 0 on failure
 */
__declspec(dllexport) DWORD WINAPI GetDriverVersion(void) {
  DWORD version = 0;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return 0;
  }

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_GET_DRIVER_VERSION, NULL, 0,
                      &version, sizeof(version), &bytesReturned, NULL)) {
    return version;
  }
  return 0;
}

/**
 * @function   GetDllVersion
 * @purpose    Get DLL version (WinRing0 format)
 * @returns    DLL version constant
 */
__declspec(dllexport) DWORD WINAPI GetDllVersion(void) {
  /* Return WinRing0 compat version for drop-in replacement */
  return SR0_COMPAT_VERSION_PACKED;
}

/*--------------------------------------------------------------------------*/
/* MSR Functions                                                            */
/*--------------------------------------------------------------------------*/

/**
 * @function   Rdmsr
 * @purpose    Read Model-Specific Register
 * @param      index - MSR index
 * @param      eax - Pointer to receive low 32 bits
 * @param      edx - Pointer to receive high 32 bits
 * @returns    TRUE on success, FALSE on failure
 */
__declspec(dllexport) BOOL WINAPI Rdmsr(DWORD index, PDWORD eax, PDWORD edx) {
  OLS_READ_MSR_INPUT input;
  ULARGE_INTEGER output;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE || eax == NULL || edx == NULL) {
    return FALSE;
  }

  input.Register = index;

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_READ_MSR, &input, sizeof(input),
                      &output, sizeof(output), &bytesReturned, NULL)) {
    *eax = output.LowPart;
    *edx = output.HighPart;
    return TRUE;
  }
  return FALSE;
}

/**
 * @function   RdmsrEx
 * @purpose    Read MSR on specific CPU (affinitized)
 * @param      index - MSR index
 * @param      eax - Pointer to receive low 32 bits
 * @param      edx - Pointer to receive high 32 bits
 * @param      affinity - CPU affinity mask
 * @returns    TRUE on success, FALSE on failure
 */
__declspec(dllexport) BOOL WINAPI RdmsrEx(DWORD index, PDWORD eax, PDWORD edx,
                                          DWORD_PTR affinity) {
  DWORD_PTR oldAffinity;
  BOOL result;

  oldAffinity = SetThreadAffinityMask(GetCurrentThread(), affinity);
  if (oldAffinity == 0) {
    return FALSE;
  }

  result = Rdmsr(index, eax, edx);

  SetThreadAffinityMask(GetCurrentThread(), oldAffinity);
  return result;
}

/**
 * @function   Wrmsr
 * @purpose    Write Model-Specific Register
 * @param      index - MSR index
 * @param      eax - Low 32 bits to write
 * @param      edx - High 32 bits to write
 * @returns    TRUE on success, FALSE on failure (including policy denial)
 */
__declspec(dllexport) BOOL WINAPI Wrmsr(DWORD index, DWORD eax, DWORD edx) {
  OLS_WRITE_MSR_INPUT input;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return FALSE;
  }

  input.Register = index;
  input.Value.LowPart = eax;
  input.Value.HighPart = edx;

  return DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_MSR, &input, sizeof(input),
                         NULL, 0, &bytesReturned, NULL);
}

/**
 * @function   WrmsrEx
 * @purpose    Write MSR on specific CPU (affinitized)
 * @param      index - MSR index
 * @param      eax - Low 32 bits to write
 * @param      edx - High 32 bits to write
 * @param      affinity - CPU affinity mask
 * @returns    TRUE on success, FALSE on failure
 */
__declspec(dllexport) BOOL WINAPI WrmsrEx(DWORD index, DWORD eax, DWORD edx,
                                          DWORD_PTR affinity) {
  DWORD_PTR oldAffinity;
  BOOL result;

  oldAffinity = SetThreadAffinityMask(GetCurrentThread(), affinity);
  if (oldAffinity == 0) {
    return FALSE;
  }

  result = Wrmsr(index, eax, edx);

  SetThreadAffinityMask(GetCurrentThread(), oldAffinity);
  return result;
}

/**
 * @function   Rdpmc
 * @purpose    Read Performance Monitor Counter
 * @param      index - PMC index
 * @param      eax - Pointer to receive low 32 bits
 * @param      edx - Pointer to receive high 32 bits
 * @returns    TRUE on success, FALSE on failure
 */
__declspec(dllexport) BOOL WINAPI Rdpmc(DWORD index, PDWORD eax, PDWORD edx) {
  OLS_READ_MSR_INPUT input;
  ULARGE_INTEGER output;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE || eax == NULL || edx == NULL) {
    return FALSE;
  }

  input.Register = index;

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_READ_PMC, &input, sizeof(input),
                      &output, sizeof(output), &bytesReturned, NULL)) {
    *eax = output.LowPart;
    *edx = output.HighPart;
    return TRUE;
  }
  return FALSE;
}

/**
 * @function   RdpmcEx
 * @purpose    Read PMC on specific CPU (affinitized)
 */
__declspec(dllexport) BOOL WINAPI RdpmcEx(DWORD index, PDWORD eax, PDWORD edx,
                                          DWORD_PTR affinity) {
  DWORD_PTR oldAffinity;
  BOOL result;

  oldAffinity = SetThreadAffinityMask(GetCurrentThread(), affinity);
  if (oldAffinity == 0) {
    return FALSE;
  }

  result = Rdpmc(index, eax, edx);

  SetThreadAffinityMask(GetCurrentThread(), oldAffinity);
  return result;
}

/*--------------------------------------------------------------------------*/
/* I/O Port Functions                                                       */
/*--------------------------------------------------------------------------*/

/**
 * @function   ReadIoPortByte
 * @purpose    Read byte from I/O port
 * @param      port - I/O port address
 * @returns    Byte value read
 */
__declspec(dllexport) BYTE WINAPI ReadIoPortByte(WORD port) {
  OLS_READ_IO_PORT_INPUT input;
  DWORD output = 0;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return 0xFF;
  }

  input.PortNumber = port;

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_READ_IO_PORT_BYTE, &input,
                      sizeof(input), &output, sizeof(output), &bytesReturned,
                      NULL)) {
    return (BYTE)output;
  }
  return 0xFF;
}

/**
 * @function   ReadIoPortWord
 * @purpose    Read word from I/O port
 */
__declspec(dllexport) WORD WINAPI ReadIoPortWord(WORD port) {
  OLS_READ_IO_PORT_INPUT input;
  DWORD output = 0;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return 0xFFFF;
  }

  input.PortNumber = port;

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_READ_IO_PORT_WORD, &input,
                      sizeof(input), &output, sizeof(output), &bytesReturned,
                      NULL)) {
    return (WORD)output;
  }
  return 0xFFFF;
}

/**
 * @function   ReadIoPortDword
 * @purpose    Read dword from I/O port
 */
__declspec(dllexport) DWORD WINAPI ReadIoPortDword(WORD port) {
  OLS_READ_IO_PORT_INPUT input;
  DWORD output = 0;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return 0xFFFFFFFF;
  }

  input.PortNumber = port;

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_READ_IO_PORT_DWORD, &input,
                      sizeof(input), &output, sizeof(output), &bytesReturned,
                      NULL)) {
    return output;
  }
  return 0xFFFFFFFF;
}

/**
 * @function   WriteIoPortByte
 * @purpose    Write byte to I/O port
 */
__declspec(dllexport) void WINAPI WriteIoPortByte(WORD port, BYTE value) {
  OLS_WRITE_IO_PORT_INPUT input;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return;
  }

  input.PortNumber = port;
  input.u.CharData = value;

  DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_IO_PORT_BYTE, &input,
                  sizeof(input), NULL, 0, &bytesReturned, NULL);
}

/**
 * @function   WriteIoPortWord
 * @purpose    Write word to I/O port
 */
__declspec(dllexport) void WINAPI WriteIoPortWord(WORD port, WORD value) {
  OLS_WRITE_IO_PORT_INPUT input;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return;
  }

  input.PortNumber = port;
  input.u.ShortData = value;

  DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_IO_PORT_WORD, &input,
                  sizeof(input), NULL, 0, &bytesReturned, NULL);
}

/**
 * @function   WriteIoPortDword
 * @purpose    Write dword to I/O port
 */
__declspec(dllexport) void WINAPI WriteIoPortDword(WORD port, DWORD value) {
  OLS_WRITE_IO_PORT_INPUT input;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return;
  }

  input.PortNumber = port;
  input.u.LongData = value;

  DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_IO_PORT_DWORD, &input,
                  sizeof(input), NULL, 0, &bytesReturned, NULL);
}

/*--------------------------------------------------------------------------*/
/* Physical Memory Functions                                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   ReadMemory
 * @purpose    Read from physical memory
 * @param      address - Physical address
 * @param      buffer - Output buffer
 * @param      count - Bytes to read
 * @param      unitSize - Unit size (1, 2, or 4)
 * @returns    Bytes read or 0 on failure
 */
__declspec(dllexport) DWORD WINAPI ReadMemory(DWORD_PTR address, PBYTE buffer,
                                              DWORD count, DWORD unitSize) {
  OLS_READ_MEMORY_INPUT input;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE || buffer == NULL || count == 0) {
    return 0;
  }

  input.Address.QuadPart = (LONGLONG)address;
  input.UnitSize = unitSize;
  input.Count = count / unitSize;

  if (DeviceIoControl(g_hDevice, IOCTL_OLS_READ_MEMORY, &input, sizeof(input),
                      buffer, count, &bytesReturned, NULL)) {
    return bytesReturned;
  }
  return 0;
}

/**
 * @function   WriteMemory
 * @purpose    Write to physical memory
 * @param      address - Physical address
 * @param      buffer - Input buffer
 * @param      count - Bytes to write
 * @param      unitSize - Unit size (1, 2, or 4)
 * @returns    Bytes written or 0 on failure
 */
__declspec(dllexport) DWORD WINAPI WriteMemory(DWORD_PTR address, PBYTE buffer,
                                               DWORD count, DWORD unitSize) {
  OLS_WRITE_MEMORY_INPUT* pInput;
  DWORD inputSize;
  DWORD bytesReturned = 0;
  BOOL result;

  if (g_hDevice == INVALID_HANDLE_VALUE || buffer == NULL || count == 0) {
    return 0;
  }

  /* Allocate input buffer with variable-length data */
  inputSize = sizeof(OLS_WRITE_MEMORY_INPUT) - 1 + count;
  pInput = (OLS_WRITE_MEMORY_INPUT*)HeapAlloc(GetProcessHeap(), 0, inputSize);
  if (pInput == NULL) {
    return 0;
  }

  pInput->Address.QuadPart = (LONGLONG)address;
  pInput->UnitSize = unitSize;
  pInput->Count = count / unitSize;
  memcpy(pInput->Data, buffer, count);

  result = DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_MEMORY, pInput, inputSize,
                           NULL, 0, &bytesReturned, NULL);

  HeapFree(GetProcessHeap(), 0, pInput);

  return result ? count : 0;
}

/*--------------------------------------------------------------------------*/
/* PCI Configuration Functions                                              */
/*--------------------------------------------------------------------------*/

/**
 * @function   SetPciMaxBusIndex
 * @purpose    Set maximum PCI bus index (WinRing0 compat, no-op in SafeRing0)
 */
__declspec(dllexport) void WINAPI SetPciMaxBusIndex(BYTE max) {
  /* Not needed - SafeRing0 validates dynamically */
  (void)max;
}

/**
 * @function   ReadPciConfigByte
 * @purpose    Read byte from PCI configuration space
 */
__declspec(dllexport) BYTE WINAPI ReadPciConfigByte(DWORD pciAddress,
                                                    BYTE regAddress) {
  DWORD value = ReadPciConfigDword(pciAddress, regAddress & 0xFC);
  return (BYTE)(value >> ((regAddress & 3) * 8));
}

/**
 * @function   ReadPciConfigWord
 * @purpose    Read word from PCI configuration space
 */
__declspec(dllexport) WORD WINAPI ReadPciConfigWord(DWORD pciAddress,
                                                    BYTE regAddress) {
  DWORD value = ReadPciConfigDword(pciAddress, regAddress & 0xFC);
  return (WORD)(value >> ((regAddress & 2) * 8));
}

/**
 * @function   ReadPciConfigDword
 * @purpose    Read dword from PCI configuration space
 */
__declspec(dllexport) DWORD WINAPI ReadPciConfigDword(DWORD pciAddress,
                                                      BYTE regAddress) {
  OLS_READ_PCI_CONFIG_INPUT input;
  DWORD output = 0xFFFFFFFF;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return 0xFFFFFFFF;
  }

  input.PciAddress = pciAddress;
  input.PciOffset = regAddress;

  DeviceIoControl(g_hDevice, IOCTL_OLS_READ_PCI_CONFIG, &input, sizeof(input),
                  &output, sizeof(output), &bytesReturned, NULL);

  return output;
}

/**
 * @function   ReadPciConfigDwordEx
 * @purpose    Read dword from extended PCI config space (PCIe)
 */
__declspec(dllexport) DWORD WINAPI ReadPciConfigDwordEx(DWORD pciAddress,
                                                        WORD regAddress) {
  OLS_READ_PCI_CONFIG_INPUT input;
  DWORD output = 0xFFFFFFFF;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return 0xFFFFFFFF;
  }

  input.PciAddress = pciAddress;
  input.PciOffset = regAddress;

  DeviceIoControl(g_hDevice, IOCTL_OLS_READ_PCI_CONFIG, &input, sizeof(input),
                  &output, sizeof(output), &bytesReturned, NULL);

  return output;
}

/**
 * @function   WritePciConfigByte
 * @purpose    Write byte to PCI configuration space
 */
__declspec(dllexport) void WINAPI WritePciConfigByte(DWORD pciAddress,
                                                     BYTE regAddress,
                                                     BYTE value) {
  /* Read-modify-write for byte access */
  DWORD current = ReadPciConfigDword(pciAddress, regAddress & 0xFC);
  DWORD shift = (regAddress & 3) * 8;
  DWORD mask = ~(0xFFUL << shift);
  current = (current & mask) | ((DWORD)value << shift);
  WritePciConfigDword(pciAddress, regAddress & 0xFC, current);
}

/**
 * @function   WritePciConfigWord
 * @purpose    Write word to PCI configuration space
 */
__declspec(dllexport) void WINAPI WritePciConfigWord(DWORD pciAddress,
                                                     BYTE regAddress,
                                                     WORD value) {
  /* Read-modify-write for word access */
  DWORD current = ReadPciConfigDword(pciAddress, regAddress & 0xFC);
  DWORD shift = (regAddress & 2) * 8;
  DWORD mask = ~(0xFFFFUL << shift);
  current = (current & mask) | ((DWORD)value << shift);
  WritePciConfigDword(pciAddress, regAddress & 0xFC, current);
}

/**
 * @function   WritePciConfigDword
 * @purpose    Write dword to PCI configuration space
 */
__declspec(dllexport) void WINAPI WritePciConfigDword(DWORD pciAddress,
                                                      BYTE regAddress,
                                                      DWORD value) {
  UCHAR inputBuf[sizeof(OLS_WRITE_PCI_CONFIG_INPUT) - 1 + sizeof(DWORD)];
  OLS_WRITE_PCI_CONFIG_INPUT* pInput = (OLS_WRITE_PCI_CONFIG_INPUT*)inputBuf;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return;
  }

  pInput->PciAddress = pciAddress;
  pInput->PciOffset = regAddress;
  *(DWORD*)pInput->Data = value;

  DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_PCI_CONFIG, pInput,
                  sizeof(inputBuf), NULL, 0, &bytesReturned, NULL);
}

/**
 * @function   WritePciConfigDwordEx
 * @purpose    Write dword to extended PCI config space (PCIe)
 */
__declspec(dllexport) void WINAPI WritePciConfigDwordEx(DWORD pciAddress,
                                                        WORD regAddress,
                                                        DWORD value) {
  UCHAR inputBuf[sizeof(OLS_WRITE_PCI_CONFIG_INPUT) - 1 + sizeof(DWORD)];
  OLS_WRITE_PCI_CONFIG_INPUT* pInput = (OLS_WRITE_PCI_CONFIG_INPUT*)inputBuf;
  DWORD bytesReturned = 0;

  if (g_hDevice == INVALID_HANDLE_VALUE) {
    return;
  }

  pInput->PciAddress = pciAddress;
  pInput->PciOffset = regAddress;
  *(DWORD*)pInput->Data = value;

  DeviceIoControl(g_hDevice, IOCTL_OLS_WRITE_PCI_CONFIG, pInput,
                  sizeof(inputBuf), NULL, 0, &bytesReturned, NULL);
}

/*--------------------------------------------------------------------------*/
/* CPUID Functions (Local, no driver needed)                                */
/*--------------------------------------------------------------------------*/

/**
 * @function   Cpuid
 * @purpose    Execute CPUID instruction
 * @param      index - CPUID leaf (EAX input)
 * @param      eax/ebx/ecx/edx - Output registers
 * @returns    TRUE always (CPUID always succeeds)
 */
__declspec(dllexport) BOOL WINAPI Cpuid(DWORD index, PDWORD eax, PDWORD ebx,
                                        PDWORD ecx, PDWORD edx) {
  int regs[4];

  if (eax == NULL || ebx == NULL || ecx == NULL || edx == NULL) {
    return FALSE;
  }

  __cpuid(regs, (int)index);

  *eax = (DWORD)regs[0];
  *ebx = (DWORD)regs[1];
  *ecx = (DWORD)regs[2];
  *edx = (DWORD)regs[3];

  return TRUE;
}

/**
 * @function   CpuidEx
 * @purpose    Execute CPUID with subleaf
 */
__declspec(dllexport) BOOL WINAPI CpuidEx(DWORD index, DWORD ecxValue,
                                          PDWORD eax, PDWORD ebx, PDWORD ecx,
                                          PDWORD edx) {
  int regs[4];

  if (eax == NULL || ebx == NULL || ecx == NULL || edx == NULL) {
    return FALSE;
  }

  __cpuidex(regs, (int)index, (int)ecxValue);

  *eax = (DWORD)regs[0];
  *ebx = (DWORD)regs[1];
  *ecx = (DWORD)regs[2];
  *edx = (DWORD)regs[3];

  return TRUE;
}

/*--------------------------------------------------------------------------*/
/* TSC Functions (Local, no driver needed)                                  */
/*--------------------------------------------------------------------------*/

/**
 * @function   Rdtsc
 * @purpose    Read timestamp counter
 */
__declspec(dllexport) BOOL WINAPI Rdtsc(PDWORD eax, PDWORD edx) {
  ULARGE_INTEGER tsc;

  if (eax == NULL || edx == NULL) {
    return FALSE;
  }

  tsc.QuadPart = __rdtsc();
  *eax = tsc.LowPart;
  *edx = tsc.HighPart;

  return TRUE;
}

/**
 * @function   RdtscEx
 * @purpose    Read TSC on specific CPU
 */
__declspec(dllexport) BOOL WINAPI RdtscEx(PDWORD eax, PDWORD edx,
                                          DWORD_PTR affinity) {
  DWORD_PTR oldAffinity;
  BOOL result;

  oldAffinity = SetThreadAffinityMask(GetCurrentThread(), affinity);
  if (oldAffinity == 0) {
    return FALSE;
  }

  result = Rdtsc(eax, edx);

  SetThreadAffinityMask(GetCurrentThread(), oldAffinity);
  return result;
}

/*--------------------------------------------------------------------------*/
/* HLT Function (BLOCKED in SafeRing0)                                      */
/*--------------------------------------------------------------------------*/

/**
 * @function   Hlt
 * @purpose    Execute HLT instruction - ALWAYS FAILS
 * @returns    FALSE (SafeRing0 blocks this for security)
 */
__declspec(dllexport) BOOL WINAPI Hlt(void) {
  /* HLT is blocked by SafeRing0 - no legitimate use case */
  return FALSE;
}

/**
 * @function   HltEx
 * @purpose    Execute HLT on specific CPU - ALWAYS FAILS
 */
__declspec(dllexport) BOOL WINAPI HltEx(DWORD_PTR affinity) {
  (void)affinity;
  return FALSE;
}

/*--------------------------------------------------------------------------*/
/* Bus Master Enable (for legacy compatibility)                             */
/*--------------------------------------------------------------------------*/

/**
 * @function   ReadDmiMemory
 * @purpose    Read SMBIOS/DMI memory region
 * @returns    Bytes read or 0 on failure
 */
__declspec(dllexport) DWORD WINAPI ReadDmiMemory(PBYTE buffer, DWORD count,
                                                 DWORD unitSize) {
  /* DMI tables are at physical address 0xF0000-0xFFFFF */
  return ReadMemory(0xF0000, buffer, count, unitSize);
}

/**
 * @function   FindDmiTablePhysicalAddress
 * @purpose    Find SMBIOS entry point table
 * @returns    Physical address or 0 if not found
 */
__declspec(dllexport) DWORD_PTR WINAPI FindDmiTablePhysicalAddress(void) {
  BYTE buffer[0x10000];
  DWORD bytesRead;
  DWORD i;

  /* Read BIOS area looking for _SM_ signature */
  bytesRead = ReadMemory(0xF0000, buffer, sizeof(buffer), 1);
  if (bytesRead == 0) {
    return 0;
  }

  /* Search for SMBIOS entry point signature */
  for (i = 0; i < bytesRead - 16; i += 16) {
    if (buffer[i] == '_' && buffer[i + 1] == 'S' && buffer[i + 2] == 'M' &&
        buffer[i + 3] == '_') {
      return 0xF0000 + i;
    }
  }

  return 0;
}
