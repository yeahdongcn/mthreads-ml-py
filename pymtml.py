##
# Python bindings for the MTML library
##
from __future__ import annotations
from dataclasses import dataclass

import string
import sys
import threading
from ctypes import *
from functools import wraps

from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from typing_extensions import TypeAlias as _TypeAlias  # Python 3.10+

## C Type mappings ##
## Constants
MTML_DEVICE_UUID_BUFFER_SIZE = 48
MTML_SYSTEM_DRIVER_VERSION_BUFFER_SIZE = 80
MTML_DEVICE_NAME_BUFFER_SIZE = 32
MTML_DEVICE_SERIAL_NUMBER_BUFFER_SIZE = 64
MTML_DEVICE_PCI_SBDF_BUFFER_SIZE = 32
MTML_DEVICE_PCI_BUS_ID_BUFFER_SIZE = 32
## Enums
_mtmlReturn_t = c_uint
MTML_SUCCESS = 0
MTML_ERROR_NOT_SUPPORTED = 4
MTML_ERROR_UNINITIALIZED = 666
MTML_ERROR_FUNCTION_NOT_FOUND = 667
MTML_ERROR_INSUFFICIENT_SIZE = 668
MTML_ERROR_GPU_IS_LOST = 669
MTML_ERROR_LIBRARY_NOT_FOUND = 670
MTML_ERROR_NO_PERMISSION = 671
MTML_ERROR_NOT_FOUND = 672
MTML_ERROR_UNKNOWN = 999

_mtmlMtLinkState_t = c_uint
MTML_MTLINK_STATE_DOWN = 0
MTML_MTLINK_STATE_UP = 1
MTML_MTLINK_STATE_DOWNGRADE = 2


## Library structures
class struct_c_mtmlLibrary_t(Structure):
    pass  # opaque handle


c_mtmlLibrary_t = POINTER(struct_c_mtmlLibrary_t)


## Device structures
class struct_c_mtmlDevice_t(Structure):
    pass  # opaque handle


c_mtmlDevice_t = POINTER(struct_c_mtmlDevice_t)

## FieldValue structures(for nvmlFieldValue_t)
class struct_c_mtmlFieldValue_t(Structure):
    pass # opaque handle

c_mtmlFieldValue_t = POINTER(struct_c_mtmlFieldValue_t)

## System structures
class struct_c_mtmlSystem_t(Structure):
    pass # opaque handle

c_mtmlSystem_t = POINTER(struct_c_mtmlSystem_t)

## Memory structures
class struct_c_mtmlMemory_t(Structure):
    pass # opaque handle

c_mtmlMemory_t = POINTER(struct_c_mtmlMemory_t)

## Gpu structures
class struct_c_mtmlGpu_t(Structure):
    pass # opaque handle

c_mtmlGpu_t = POINTER(struct_c_mtmlGpu_t)

## Vpu structures
class struct_c_mtmlVpu_t(Structure):
    pass

c_mtmlVpu_t = POINTER(struct_c_mtmlVpu_t)

class mtmlFriendlyObject(object):
    def __init__(self, dictionary):
        for x in dictionary:
            setattr(self, x, dictionary[x])
    def __str__(self):
        return self.__dict__.__str__()

def mtmlStructToFriendlyObject(struct):
    d = {}
    for x in struct._fields_:
        key = x[0]
        value = getattr(struct, key)
        # only need to convert from bytes if bytes, no need to check python version.
        d[key] = value.decode() if isinstance(value, bytes) else value
    obj = mtmlFriendlyObject(d)
    return obj


class _PrintableStructure(Structure):
    """
    Abstract class that produces nicer __str__ output than ctypes.Structure.
    e.g. instead of:
      >>> print str(obj)
      <class_name object at 0x7fdf82fef9e0>
    this class will print
      class_name(field_name: formatted_value, field_name: formatted_value)

    _fmt_ dictionary of <str _field_ name> -> <str format>
    e.g. class that has _field_ 'hex_value', c_uint could be formatted with
      _fmt_ = {"hex_value" : "%08X"}
    to produce nicer output.
    Default formatting string for all fields can be set with key "<default>" like:
      _fmt_ = {"<default>" : "%d MHz"} # e.g all values are numbers in MHz.
    If not set it's assumed to be just "%s"

    Exact format of returned str from this class is subject to change in the future.
    """

    _fmt_ = {}

    def __str__(self):
        result = []
        for x in self._fields_:
            key = x[0]
            value = getattr(self, key)
            fmt = "%s"
            if key in self._fmt_:
                fmt = self._fmt_[key]
            elif "<default>" in self._fmt_:
                fmt = self._fmt_["<default>"]
            result.append(("%s: " + fmt) % (key, value))
        return self.__class__.__name__ + "(" + ", ".join(result) + ")"

    def __getattribute__(self, name):
        res = super(_PrintableStructure, self).__getattribute__(name)
        # need to convert bytes to unicode for python3 don't need to for python2
        # Python 2 strings are of both str and bytes
        # Python 3 strings are not of type bytes
        # ctypes should convert everything to the correct values otherwise
        if isinstance(res, bytes):
            if isinstance(res, str):
                return res
            return res.decode()
        return res

    def __setattr__(self, name, value):
        if isinstance(value, str):
            # encoding a python2 string returns the same value, since python2 strings are bytes already
            # bytes passed in python3 will be ignored.
            value = value.encode()
        super(_PrintableStructure, self).__setattr__(name, value)


## MtLink structures
class c_mtmlMtLinkSpec_t(_PrintableStructure):
    _fields_ = [
        ("version", c_uint),
        ("bandWidth", c_uint),
        ("linkNum", c_uint),
        ("rsvd", c_uint * 4),
    ]

class c_mtmlPciInfo_t(_PrintableStructure):
    _fields_ = [
        ("sbdf", c_char * MTML_DEVICE_PCI_SBDF_BUFFER_SIZE),
        ("segment", c_uint),
        ("bus", c_uint),
        ("device", c_uint),
        ("pciDeviceId", c_uint),
        ("busWidth", c_uint),
        ("pciMaxSpeed", c_float),
        ("pciCurSpeed", c_float),
        ("pciMaxWidth", c_uint),
        ("pciCurWidth", c_uint),
        ("pciMaxGen", c_uint),
        ("pciCurGen", c_uint),
        ("busId", c_char * MTML_DEVICE_PCI_BUS_ID_BUFFER_SIZE),
        ("rsvd", c_uint * 6)
    ]


## Lib loading ##
mtmlLib = None
libLoadLock = threading.Lock()
libHandle = c_mtmlLibrary_t()
_mtmlLib_refcount = 0  # Incremented on each mtmlInit and decremented on mtmlShutdown


## Error Checking ##
class MTMLError(Exception):
    _valClassMapping = dict()
    # List of currently known error codes
    _errcode_to_string = {
        MTML_ERROR_UNINITIALIZED: "Uninitialized",
        MTML_ERROR_NOT_SUPPORTED: "Not Supported",
        MTML_ERROR_FUNCTION_NOT_FOUND: "Function Not Found",
        MTML_ERROR_UNKNOWN: "Unknown Error",
        MTML_ERROR_INSUFFICIENT_SIZE: "Insufficient Size",
        MTML_ERROR_GPU_IS_LOST: "Gpu Is Lost",
        MTML_ERROR_LIBRARY_NOT_FOUND: "Library Not Found",
        MTML_ERROR_NO_PERMISSION: "No Permission",
        MTML_ERROR_NOT_FOUND: "Not Found",
    }

    def __new__(typ, value):
        """
        Maps value to a proper subclass of MTMLError.
        See _extractMTMLErrorsAsClasses function for more details
        """
        if typ == MTMLError:
            typ = MTMLError._valClassMapping.get(value, typ)
        obj = Exception.__new__(typ)
        obj.value = value
        return obj

    def __str__(self):
        try:
            if self.value not in MTMLError._errcode_to_string:
                MTMLError._errcode_to_string[self.value] = str(
                    mtmlErrorString(self.value)
                )
            return MTMLError._errcode_to_string[self.value]
        except MTMLError:
            return "MTML Error with code %d" % self.value

    def __eq__(self, other):
        return self.value == other.value


def _extractMTMLErrorsAsClasses():
    """
    Generates a hierarchy of classes on top of MTMLError class.

    Each MTML Error gets a new MTMLError subclass. This way try,except blocks can filter appropriate
    exceptions more easily.

    MTMLError is a parent class. Each MTML_ERROR_* gets it's own subclass.
    e.g. MTML_ERROR_ALREADY_INITIALIZED will be turned into MTMLError_AlreadyInitialized
    """
    this_module = sys.modules[__name__]
    mtmlErrorsNames = [x for x in dir(this_module) if x.startswith("MTML_ERROR_")]
    for err_name in mtmlErrorsNames:
        # e.g. Turn MTML_ERROR_ALREADY_INITIALIZED into MTMLError_AlreadyInitialized
        class_name = "MTMLError_" + string.capwords(
            err_name.replace("MTML_ERROR_", ""), "_"
        ).replace("_", "")
        err_val = getattr(this_module, err_name)

        def gen_new(val):
            def new(typ, *args):
                obj = MTMLError.__new__(typ, val)
                return obj

            return new

        new_error_class = type(class_name, (MTMLError,), {"__new__": gen_new(err_val)})
        new_error_class.__module__ = __name__
        setattr(this_module, class_name, new_error_class)
        MTMLError._valClassMapping[err_val] = new_error_class


_extractMTMLErrorsAsClasses()


def _mtmlCheckReturn(ret):
    if ret != MTML_SUCCESS:
        raise MTMLError(ret)
    return ret


## Function access ##
_mtmlGetFunctionPointer_cache = (
    dict()
)  # function pointers are cached to prevent unnecessary libLoadLock locking


def _mtmlGetFunctionPointer(name):
    global mtmlLib

    if name in _mtmlGetFunctionPointer_cache:
        return _mtmlGetFunctionPointer_cache[name]

    libLoadLock.acquire()
    try:
        # ensure library was loaded
        if mtmlLib == None:
            raise MTMLError(MTML_ERROR_FUNCTION_NOT_FOUND)
        try:
            _mtmlGetFunctionPointer_cache[name] = getattr(mtmlLib, name)
            return _mtmlGetFunctionPointer_cache[name]
        except AttributeError:
            raise MTMLError(MTML_ERROR_FUNCTION_NOT_FOUND)
    finally:
        # lock is always freed
        libLoadLock.release()


## string/bytes conversion for ease of use
def convertStrBytes(func):
    """
    In python 3, strings are unicode instead of bytes, and need to be converted for ctypes
    Args from caller: (1, 'string', <__main__.c_mtmlDevice_t at 0xFFFFFFFF>)
    Args passed to function: (1, b'string', <__main__.c_mtmlDevice_t at 0xFFFFFFFF)>
    ----
    Returned from function: b'returned string'
    Returned to caller: 'returned string'
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # encoding a str returns bytes in python 2 and 3
        args = [arg.encode() if isinstance(arg, str) else arg for arg in args]
        res = func(*args, **kwargs)
        # In python 2, str and bytes are the same
        # In python 3, str is unicode and should be decoded.
        # Ctypes handles most conversions, this only effects c_char and char arrays.
        if isinstance(res, bytes):
            if isinstance(res, str):
                return res
            return res.decode()
        return res

    if sys.version_info >= (3,):
        return wrapper
    return func


## C function wrappers ##
def _LoadMtmlLibrary():
    """
    Load the library if it isn't loaded already
    """
    global mtmlLib

    if mtmlLib == None:
        # lock to ensure only one caller loads the library
        libLoadLock.acquire()

        try:
            # ensure the library still isn't loaded
            if mtmlLib == None:
                try:
                    # assume linux
                    mtmlLib = CDLL("libmtml.so")
                except OSError as ose:
                    _mtmlCheckReturn(MTML_ERROR_FUNCTION_NOT_FOUND)
                if mtmlLib == None:
                    _mtmlCheckReturn(MTML_ERROR_FUNCTION_NOT_FOUND)
        finally:
            # lock is always freed
            libLoadLock.release()


def mtmlLibraryInit():
    _LoadMtmlLibrary()

    #
    # Initialize the library
    #
    global libHandle
    fn = _mtmlGetFunctionPointer("mtmlLibraryInit")
    ret = fn(byref(libHandle))
    _mtmlCheckReturn(ret)

    # Atomically update refcount
    global _mtmlLib_refcount
    libLoadLock.acquire()
    _mtmlLib_refcount += 1
    libLoadLock.release()
    return None


def mtmlLibraryShutDown():
    #
    # Leave the library loaded, but shutdown the interface
    #
    global libHandle
    if libHandle is None:
        return None

    fn = _mtmlGetFunctionPointer("mtmlLibraryShutDown")
    ret = fn(libHandle)
    _mtmlCheckReturn(ret)

    # Atomically update refcount
    global _mtmlLib_refcount
    libLoadLock.acquire()
    if 0 < _mtmlLib_refcount:
        _mtmlLib_refcount -= 1
    libLoadLock.release()
    return None


@convertStrBytes
def mtmlErrorString(result):
    fn = _mtmlGetFunctionPointer("mtmlErrorString")
    fn.restype = c_char_p  # otherwise return is an int
    ret = fn(result)
    return ret


def mtmlLibraryCountDevice():
    global libHandle
    c_count = c_uint()
    fn = _mtmlGetFunctionPointer("mtmlLibraryCountDevice")
    ret = fn(libHandle, byref(c_count))
    _mtmlCheckReturn(ret)
    return c_count.value


def mtmlLibraryInitDeviceByIndex(index):
    global libHandle
    c_index = c_uint(index)
    c_device = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlLibraryInitDeviceByIndex")
    ret = fn(libHandle, c_index, byref(c_device))
    _mtmlCheckReturn(ret)
    return c_device

@convertStrBytes
def mtmlLibraryInitDeviceByUuid(uuid):
    global libHandle
    c_uuid = c_char_p(uuid)
    c_device = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlLibraryInitDeviceByUuid")
    ret = fn(libHandle, c_uuid, byref(c_device))
    _mtmlCheckReturn(ret)
    return c_device

@convertStrBytes
def mtmlLibraryInitDeviceByPciSbdf(pciSbdf):
    global libHandle
    c_pciSbdf = c_char_p(pciSbdf)
    c_device = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlLibraryInitDeviceByPciSbdf")
    ret = fn(libHandle, c_pciSbdf, byref(c_device))
    _mtmlCheckReturn(ret)
    return c_device

def mtmlLibraryInitSystem():
    global libHandle
    c_system = c_mtmlSystem_t()
    fn = _mtmlGetFunctionPointer("mtmlLibraryInitSystem")
    ret = fn(libHandle, byref(c_system))
    _mtmlCheckReturn(ret)
    return c_system

def mtmlDeviceInitMemory(device):
    global libHandle
    c_memory = c_mtmlMemory_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceInitMemory")
    ret = fn(device, byref(c_memory))
    _mtmlCheckReturn(ret)
    return c_memory

def mtmlDeviceInitGpu(device):
    global libHandle
    c_gpu = c_mtmlGpu_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceInitGpu")
    ret = fn(device, byref(c_gpu))
    _mtmlCheckReturn(ret)
    return c_gpu

def mtmlDeviceInitVpu(device):
    global libHandle
    c_vpu = c_mtmlVpu_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceInitVpu")
    ret = fn(device, byref(c_vpu))
    _mtmlCheckReturn(ret)
    return c_vpu

def mtmlDeviceGetIndex(device):
    global libHandle
    c_index = c_uint()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetIndex")
    ret = fn(device, byref(c_index))
    _mtmlCheckReturn(ret)
    return c_index.value

@convertStrBytes
def mtmlDeviceGetName(device):
    global libHandle
    c_name = create_string_buffer(MTML_DEVICE_NAME_BUFFER_SIZE)
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetName")
    ret = fn(device, c_name, c_uint(MTML_DEVICE_NAME_BUFFER_SIZE))
    _mtmlCheckReturn(ret)
    return c_name.value

def mtmlDeviceGetPciInfo(device):
    global libHandle
    c_pciinfo = c_mtmlPciInfo_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetPciInfo")
    ret = fn(device, byref(c_pciinfo))
    _mtmlCheckReturn(ret)
    return c_pciinfo

def mtmlDeviceGetSerialNumber(device):
    global libHandle
    c_serial = create_string_buffer(MTML_DEVICE_SERIAL_NUMBER_BUFFER_SIZE)
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetSerialNumber")
    ret = fn(device, c_uint(MTML_DEVICE_SERIAL_NUMBER_BUFFER_SIZE), c_serial)
    _mtmlCheckReturn(ret)
    return c_serial.value

def mtmlDeviceGetPowerUsage(device):
    global libHandle
    c_power = c_uint()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetPowerUsage")
    ret = fn(device, byref(c_power))
    _mtmlCheckReturn(ret)
    return c_power.value

@convertStrBytes
def mtmlDeviceGetUUID(device):
    c_uuid = (c_char * MTML_DEVICE_UUID_BUFFER_SIZE)()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetUUID")
    ret = fn(device, byref(c_uuid), MTML_DEVICE_UUID_BUFFER_SIZE)
    _mtmlCheckReturn(ret)
    return c_uuid.value


def mtmlDeviceGetMtLinkSpec(device):
    c_mtLinkSpec = c_mtmlMtLinkSpec_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkSpec")
    ret = fn(device, byref(c_mtLinkSpec))
    _mtmlCheckReturn(ret)
    return c_mtLinkSpec


def mtmlDeviceGetMtLinkState(device, linkIndex):
    c_mtLinkState = _mtmlMtLinkState_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkState")
    ret = fn(device, linkIndex, byref(c_mtLinkState))
    _mtmlCheckReturn(ret)
    return c_mtLinkState.value


def mtmlDeviceGetMtLinkRemoteDevice(device, linkIndex):
    c_device = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkRemoteDevice")
    ret = fn(device, linkIndex, byref(c_device))
    _mtmlCheckReturn(ret)
    return c_device

def mtmlMemoryGetTotal(memory):
    global libHandle
    c_total = c_uint64()
    fn = _mtmlGetFunctionPointer("mtmlMemoryGetTotal")
    ret = fn(memory, byref(c_total))
    _mtmlCheckReturn(ret)
    return c_total.value

def mtmlMemoryGetUsed(memory):
    global libHandle
    c_used = c_uint64()
    fn = _mtmlGetFunctionPointer("mtmlMemoryGetUsed")
    ret = fn(memory, byref(c_used))
    _mtmlCheckReturn(ret)
    return c_used.value

def mtmlMemoryGetClock(device):
    global libHandle
    c_clock = c_uint()
    c_memory = mtmlDeviceInitMemory(device)
    fn = _mtmlGetFunctionPointer("mtmlMemoryGetClock")
    ret = fn(c_memory, byref(c_clock))
    _mtmlCheckReturn(ret)
    return c_clock.value

def mtmlMemoryGetMaxClock(device):
    global libHandle
    c_clock = c_uint()
    c_memory = mtmlDeviceInitMemory(device)
    fn = _mtmlGetFunctionPointer("mtmlMemoryGetMaxClock")
    ret = fn(c_memory, byref(c_clock))
    _mtmlCheckReturn(ret)
    return c_clock.value

def mtmlMemoryGetUtilization(device):
    global libHandle
    utilization = c_uint()
    c_memory = mtmlDeviceInitMemory(device)
    fn = _mtmlGetFunctionPointer("mtmlMemoryGetUtilization")
    ret = fn(c_memory, byref(utilization))
    _mtmlCheckReturn(ret)
    return utilization.value

def mtmlGpuGetUtilization(device):
    global libHandle
    utilization = c_uint()
    c_gpu = mtmlDeviceInitGpu(device)
    fn = _mtmlGetFunctionPointer("mtmlGpuGetUtilization")
    ret = fn(c_gpu, byref(utilization))
    _mtmlCheckReturn(ret)
    return utilization.value

def mtmlGpuGetClock(device):
    global libHandle
    c_clock = c_uint()
    gpu = mtmlDeviceInitGpu(device)
    fn = _mtmlGetFunctionPointer("mtmlGpuGetClock")
    ret = fn(gpu, byref(c_clock))
    _mtmlCheckReturn(ret)
    return c_clock.value

def mtmlGpuGetMaxClock(device):
    global libHandle
    c_clock = c_uint()
    c_gpu = mtmlDeviceInitGpu(device)
    fn = _mtmlGetFunctionPointer("mtmlGpuGetMaxClock")
    ret = fn(c_gpu, byref(c_clock))
    _mtmlCheckReturn(ret)
    return c_clock.value

def mtmlGpuGetTemperature(device):
    global libHandle
    c_temp = c_uint()
    c_gpu = mtmlDeviceInitGpu(device)
    fn = _mtmlGetFunctionPointer("mtmlGpuGetTemperature")
    ret = fn(c_gpu, byref(c_temp))
    _mtmlCheckReturn(ret)
    return c_temp.value

def mtmlVpuGetClock(device):
    global libHandle
    c_clock = c_uint()
    c_vpu = mtmlDeviceInitVpu(device)
    fn = _mtmlGetFunctionPointer("mtmlVpuGetClock")
    ret = fn(c_vpu, byref(c_clock))
    _mtmlCheckReturn(ret)
    return c_clock.value

def mtmlVpuGetMaxClock(device):
    global libHandle
    c_clock = c_uint()
    c_vpu = mtmlDeviceInitVpu(device)
    fn = _mtmlGetFunctionPointer("mtmlVpuGetMaxClock")
    ret = fn(c_vpu, byref(c_clock))
    _mtmlCheckReturn(ret)
    return c_clock.value

def mtmlSystemGetDriverVersion(system):
    c_version = create_string_buffer(MTML_SYSTEM_DRIVER_VERSION_BUFFER_SIZE)
    fn = _mtmlGetFunctionPointer("mtmlSystemGetDriverVersion")
    ret = fn(system, c_version, c_uint(MTML_SYSTEM_DRIVER_VERSION_BUFFER_SIZE))
    _mtmlCheckReturn(ret)
    return c_version.value

# nvml wrapper layer ###########################################################
# NVML constants and types###########################################
NVML_SUCCESS = MTML_SUCCESS
NVML_ERROR_NOT_SUPPORTED = MTML_ERROR_NOT_SUPPORTED
NVML_ERROR_UNINITIALIZED = MTML_ERROR_UNINITIALIZED
NVML_ERROR_FUNCTION_NOT_FOUND = MTML_ERROR_FUNCTION_NOT_FOUND
NVML_ERROR_INSUFFICIENT_SIZE = MTML_ERROR_INSUFFICIENT_SIZE
NVML_ERROR_GPU_IS_LOST = MTML_ERROR_GPU_IS_LOST
NVML_ERROR_LIBRARY_NOT_FOUND = MTML_ERROR_LIBRARY_NOT_FOUND
NVML_ERROR_NO_PERMISSION = MTML_ERROR_NO_PERMISSION
NVML_ERROR_NOT_FOUND = MTML_ERROR_NOT_FOUND
NVML_ERROR_UNKNOWN = MTML_ERROR_UNKNOWN


_nvmlClockType_t = c_uint
NVML_CLOCK_GRAPHICS  = 0
NVML_CLOCK_SM        = 1
NVML_CLOCK_MEM       = 2
NVML_CLOCK_VIDEO     = 3
NVML_CLOCK_COUNT     = 4

_nvmlTemperatureSensors_t = c_uint
NVML_TEMPERATURE_GPU     = 0
NVML_TEMPERATURE_COUNT   = 1

_nvmlDriverModel_t = c_uint
NVML_DRIVER_WDDM       = 0
NVML_DRIVER_WDM        = 1
NVML_DRIVER_MCDM       = 2

_nvmlMemoryErrorType_t = c_uint
NVML_MEMORY_ERROR_TYPE_CORRECTED   = 0
NVML_MEMORY_ERROR_TYPE_UNCORRECTED = 1
NVML_MEMORY_ERROR_TYPE_COUNT       = 2

_nvmlEccCounterType_t = c_uint
NVML_VOLATILE_ECC      = 0
NVML_AGGREGATE_ECC     = 1
NVML_ECC_COUNTER_TYPE_COUNT = 2

_nvmlComputeMode_t = c_uint
NVML_COMPUTEMODE_DEFAULT           = 0
NVML_COMPUTEMODE_EXCLUSIVE_THREAD  = 1  ## Support Removed
NVML_COMPUTEMODE_PROHIBITED        = 2
NVML_COMPUTEMODE_EXCLUSIVE_PROCESS = 3
NVML_COMPUTEMODE_COUNT             = 4

_nvmlPcieUtilCounter_t = c_uint
NVML_PCIE_UTIL_TX_BYTES = 0
NVML_PCIE_UTIL_RX_BYTES = 1
NVML_PCIE_UTIL_COUNT = 2

NVML_NVLINK_MAX_LINKS = 18
# NVLink Link Count
NVML_FI_DEV_NVLINK_LINK_COUNT = 91

# NVLink Throughput Counters
NVML_FI_DEV_NVLINK_THROUGHPUT_DATA_TX = 138 # NVLink TX Data throughput in KiB
NVML_FI_DEV_NVLINK_THROUGHPUT_DATA_RX = 139 # NVLink RX Data throughput in KiB
NVML_FI_DEV_NVLINK_THROUGHPUT_RAW_TX  = 140 # NVLink TX Data + protocol overhead in KiB
NVML_FI_DEV_NVLINK_THROUGHPUT_RAW_RX  = 141 # NVLink RX Data + protocol overhead in KiB

_nvmlValueType_t = c_uint
NVML_VALUE_TYPE_DOUBLE = 0
NVML_VALUE_TYPE_UNSIGNED_INT = 1
NVML_VALUE_TYPE_UNSIGNED_LONG = 2
NVML_VALUE_TYPE_UNSIGNED_LONG_LONG = 3
NVML_VALUE_TYPE_SIGNED_LONG_LONG = 4
NVML_VALUE_TYPE_SIGNED_INT = 5
NVML_VALUE_TYPE_UNSIGNED_SHORT = 6
NVML_VALUE_TYPE_COUNT = 7

NVMLError_FunctionNotFound: _TypeAlias = MTMLError_FunctionNotFound
NVMLError_GpuIsLost: _TypeAlias = MTMLError_GpuIsLost
NVMLError_InvalidArgument : _TypeAlias = MTMLError_NotFound
NVMLError_LibraryNotFound : _TypeAlias = MTMLError_LibraryNotFound
NVMLError_NoPermission : _TypeAlias = MTMLError_NoPermission
NVMLError_NotFound : _TypeAlias = MTMLError_NotFound
NVMLError_NotSupported : _TypeAlias = MTMLError_NotSupported
NVMLError_Unknown : _TypeAlias = MTMLError_Unknown

c_nvmlFieldValue_t = c_mtmlFieldValue_t
c_nvmlDevice_t = c_mtmlDevice_t

@dataclass(frozen=True)
class NVMLMemoryInfo:
    total: int
    free: int
    used: int

@dataclass(frozen=True)
class NVMLUtilization:
    gpu: int
    memory: int

class NVMLError(MTMLError):
    def __new__(typ, value):
        obj = super().__new__(MTMLError, value)

        if not isinstance(obj, NVMLError):
            obj.__class__ = NVMLError
        
        return obj

def nvmlStructToFriendlyObject(struct):
    return mtmlStructToFriendlyObject(struct)

def nvmlInit():
    return nvmlInitWithFlags(0)

def nvmlInitWithFlags(flags):
    return mtmlLibraryInit()

def nvmlShutdown():
    return mtmlLibraryShutDown()

def nvmlExceptionClass(nvmlErrorCode):
    if nvmlErrorCode not in NVMLError._valClassMapping:
        raise ValueError('nvmlErrorCode %s is not valid' % nvmlErrorCode)
    return NVMLError._valClassMapping[nvmlErrorCode]

def nvmlSystemGetDriverVersion():
    c_system = mtmlLibraryInitSystem()
    return mtmlSystemGetDriverVersion(c_system)


def nvmlDeviceGetCount():
    return mtmlLibraryCountDevice()

def nvmlDeviceGetHandleByIndex(index):
    return mtmlLibraryInitDeviceByIndex(index)
    
def nvmlDeviceGetHandleByUuid(uuid):
    return mtmlLibraryInitDeviceByUuid(uuid)

def nvmlDeviceGetHandleByPciBusId(pciBusId):
    return mtmlLibraryInitDeviceByPciSbdf(pciBusId)

def nvmlDeviceGetIndex(device):
    return mtmlDeviceGetIndex(device)

def nvmlDeviceGetName(device):
    return mtmlDeviceGetName(device)

def nvmlDeviceGetUUID(device):
    return mtmlDeviceGetUUID(device)

def nvmlDeviceGetPciInfo(device):
    return mtmlDeviceGetPciInfo(device)


def nvmlDeviceGetSerial(device):
    return mtmlDeviceGetSerialNumber(device)

def nvmlDeviceGetMemoryInfo(device):
    handle = mtmlDeviceInitMemory(device)
    total = mtmlMemoryGetTotal(handle)
    used = mtmlMemoryGetUsed(handle)
    return NVMLMemoryInfo(total=total, free=(total - used), used=used)

def nvmlDeviceGetUtilizationRates(device):
    gpu = mtmlGpuGetUtilization(device)
    memory = mtmlMemoryGetUtilization(device)
    return NVMLUtilization(gpu=gpu, memory=memory)

def nvmlDeviceGetClockInfo(device, type):
    if type == NVML_CLOCK_GRAPHICS:
        return mtmlGpuGetClock(device)
    elif type == NVML_CLOCK_VIDEO:
        return mtmlVpuGetClock(device)
    elif type == NVML_CLOCK_MEM:
        return mtmlMemoryGetClock(device)
    else:
        # SM is not support
        return 0
    
def nvmlDeviceGetMaxClockInfo(device, type):
    if type == NVML_CLOCK_GRAPHICS:
        # GPU is Not Support
        return 0
    elif type == NVML_CLOCK_VIDEO:
        return mtmlVpuGetMaxClock(device)
    elif type == NVML_CLOCK_MEM:
        return mtmlMemoryGetMaxClock(device)
    else:
        # SM is not support
        return 0
    
def nvmlDeviceGetTemperature(device, type):
    return mtmlGpuGetTemperature(device)

def nvmlDeviceGetPowerUsage(device):
    return mtmlDeviceGetPowerUsage(device)

# cannot expose this function directly since _nvmlGetFunctionPointer will retrive the function pointer from the mtml library directly.
# it will cause a MTMLError_FunctionNotFound exception when we get nvml function pointer from the mtml library.
# def _nvmlGetFunctionPointer(name):
#     return _mtmlGetFunctionPointer(name)

def nvmlDeviceGetFanSpeed(device):
    # Not Support
    return 0

def nvmlDeviceGetBAR1MemoryInfo(device):
    # Not Support
    return 'N/A'

def nvmlDeviceGetEncoderUtilization(device):
    # Not Support
    # [utilization, samplingPeriodUs]
    return [0, 0]

def nvmlDeviceGetDecoderUtilization(device):
    # Not Support
    return [0, 0]

def nvmlSystemGetCudaDriverVersion():
    # Not Support
    return 0

def nvmlDeviceGetDisplayMode(device):
    # Not Support
    return 0

def nvmlDeviceGetCurrentDriverModel(device):
    # Not Support
    return 3

def nvmlDeviceGetPersistenceMode(device):
    # Not Support
    return 0

def nvmlDeviceGetPerformanceState(device):
    # Not Support
    return 'N/A'

def nvmlDeviceGetTotalEccErrors(device, errorType, counterType):
    # Not Support
    return 0

def nvmlDeviceGetPowerManagementLimit(device):
    # Not Support
    return 0

def nvmlDeviceGetPcieThroughput(device, type):
    # Not Support
    return 0

def nvmlDeviceGetFieldValues(handle, fieldIds):
    # Not Support
    return []

def nvmlDeviceGetDisplayActive(device):
    # not support
    return 0

def nvmlDeviceGetComputeMode(device):
    # not support
    return 5

def nvmlDeviceGetCudaComputeCapability(device):
    # Not Support
    # (major, minor)
    return (0, 0)

def nvmlDeviceIsMigDeviceHandle(device):
    # not support
    return 0

def nvmlDeviceGetMigMode(device):
    # Not Support
    # [currentMode, pendingMode]
    return [0, 0]

def nvmlDeviceGetComputeRunningProcesses(device):
    # Not Support
    return []

def nvmlDeviceGetGraphicsRunningProcesses(device):
    # Not Support
    return []

def nvmlDeviceGetProcessUtilization(device, timeStamp):
    # Not Support
    return []

def nvmlDeviceGetMaxMigDeviceCount(device):
    # Not Support
    return 0

def nvmlDeviceGetMigDeviceHandleByIndex(device, index):
    # Not Support
    return 'N/A'

def nvmlDeviceGetDeviceHandleFromMigDeviceHandle(device):
    # Not Support
    return 'N/A'

def nvmlDeviceGetGpuInstanceId(device):
    # Not Support
    return 0

def nvmlDeviceGetComputeInstanceId(device):
    # Not Support
    return 0