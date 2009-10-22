
from __future__ import division

import struct

from erlastic.constants import *
from erlastic.types import *

__all__ = ["ErlangTermEncoder", "ErlangTermDecoder", "EncodingError"]

class EncodingError(Exception):
    pass

class ErlangTermDecoder(object):
    def __init__(self, encoding=None):
        self.encoding = encoding

    def decode(self, bytes, offset=0):
        version = ord(bytes[offset])
        if version != FORMAT_VERSION:
            raise EncodingError("Bad version number. Expected %d found %d" % (FORMAT_VERSION, version))
        return self._decode(bytes, offset+1)[0]

    def _decode(self, bytes, offset=0):
        tag = bytes[offset]
        offset += 1
        if tag == SMALL_INTEGER_EXT:
            return ord(bytes[offset]), offset+1
        elif tag == INTEGER_EXT:
            return struct.unpack(">l", bytes[offset:offset+4])[0], offset+4
        elif tag == FLOAT_EXT:
            return float(bytes[offset:offset+31].split('\x00', 1)[0]), offset+31
        elif tag == NEW_FLOAT_EXT:
            return struct.unpack(">d", bytes[offset:offset+8])[0], offset+8
        elif tag in (ATOM_EXT, SMALL_ATOM_EXT):
            len_n = 2 if tag == ATOM_EXT else 1
            atom_len = struct.unpack(">H", bytes[offset:offset+len_n])[0]
            atom = bytes[offset+len_n:offset+len_n+atom_len]
            offset += len_n+atom_len
            if atom == "true":
                return True, offset
            elif atom == "false":
                return False, offset
            return Atom(atom), offset
        elif tag in (SMALL_TUPLE_EXT, LARGE_TUPLE_EXT):
            if tag == SMALL_TUPLE_EXT:
                arity = ord(bytes[offset])
                offset += 1
            else:
                arity = struct.unpack(">L", bytes[offset:offset+4])[0]
                offset += 4

            items = []
            for i in range(arity):
                val, offset = self._decode(bytes, offset)
                items.append(val)
            return tuple(items), offset
        elif tag == NIL_EXT:
            return [], offset
        elif tag == STRING_EXT:
            length = struct.unpack(">H", bytes[offset:offset+2])[0]
            st = bytes[offset+2:offset+2+length]
            if self.encoding:
                try:
                    st = st.decode(self.encoding)
                except UnicodeError:
                    st = [ord(x) for x in st]
            else:
                st = [ord(x) for x in st]
            return st, offset+2+length
        elif tag == LIST_EXT:
            length = struct.unpack(">L", bytes[offset:offset+4])[0]
            offset += 4
            items = []
            for i in range(length):
                val, offset = self._decode(bytes, offset)
                items.append(val)
            tail, offset = self._decode(bytes, offset)
            if tail != []:
                # TODO: Not sure what to do with the tail
                raise NotImplementedError("Lists with non empty tails are not supported")
            return items, offset
        elif tag == BINARY_EXT:
            length = struct.unpack(">L", bytes[offset:offset+4])[0]
            return bytes[offset+4:offset+4+length], offset+4+length
        elif tag in (SMALL_BIG_EXT, LARGE_BIG_EXT):
            if tag == SMALL_BIG_EXT:
                n = ord(bytes[offset])
                offset += 1
            else:
                n = struct.unpack(">L", bytes[offset:offset+4])[0]
                offset += 4
            sign = ord(bytes[offset])
            offset += 1
            b = 1
            val = 0
            for i in range(n):
                val += ord(bytes[offset]) * b
                b <<= 8
                offset += 1
            if sign != 0:
                val = -val
            return val, offset
        elif tag == REFERENCE_EXT:
            node, offset = self._decode(bytes, offset)
            if not isinstance(node, Atom):
                raise EncodingError("Expected atom while parsing REFERENCE_EXT, found %r instead" % node)
            reference_id, creation = struct.unpack(">LB", bytes[offset:offset+5])
            return Reference(node, [reference_id], creation), offset+5
        elif tag == NEW_REFERENCE_EXT:
            id_len = struct.unpack(">H", bytes[offset:offset+2])[0]
            node, offset = self._decode(bytes, offset+2)
            if not isinstance(node, Atom):
                raise EncodingError("Expected atom while parsing NEW_REFERENCE_EXT, found %r instead" % node)
            creation = ord(bytes[offset])
            reference_id = struct.unpack(">%dL" % id_len, bytes[offset+1:offset+1+4*id_len])
            return Reference(node, reference_id, creation), offset+1+4*id_len
        elif tag == PORT_EXT:
            node, offset = self._decode(bytes, offset)
            if not isinstance(node, Atom):
                raise EncodingError("Expected atom while parsing PORT_EXT, found %r instead" % node)
            port_id, creation = struct.unpack(">LB", bytes[offset:offset+5])
            return Port(node, port_id, creation), offset+5
        elif tag == PID_EXT:
            node, offset = self._decode(bytes, offset)
            if not isinstance(node, Atom):
                raise EncodingError("Expected atom while parsing PID_EXT, found %r instead" % node)
            pid_id, serial, creation = struct.unpack(">LLB", bytes[offset:offset+9])
            return PID(node, pid_id, serial, creation), offset+9
        elif tag == EXPORT_EXT:
            module, offset = self._decode(bytes, offset)
            if not isinstance(module, Atom):
                raise EncodingError("Expected atom while parsing EXPORT_EXT, found %r instead" % module)
            function, offset = self._decode(bytes, offset)
            if not isinstance(function, Atom):
                raise EncodingError("Expected atom while parsing EXPORT_EXT, found %r instead" % function)
            arity, offset = self._decode(bytes, offset)
            if not isinstance(arity, int):
                raise EncodingError("Expected integer while parsing EXPORT_EXT, found %r instead" % arity)
            return Export(module, function, arity), offset+1
        else:
            raise NotImplementedError("Unsupported tag %d" % ord(tag))

class ErlangTermEncoder(object):
    def __init__(self, encoding="utf-8", unicode_type="binary"):
        self.encoding = encoding
        self.unicode_type = unicode_type

    def encode(self, obj):
        bytes = [chr(FORMAT_VERSION)]
        self._encode(obj, bytes)
        return "".join(bytes)

    def _encode(self, obj, bytes):
        if obj is False:
            bytes += [ATOM_EXT, struct.pack(">H", 5), "false"]
        elif obj is True:
            bytes += [ATOM_EXT, struct.pack(">H", 4), "true"]
        elif isinstance(obj, (int, long)):
            if 0 <= obj <= 255:
                bytes += [SMALL_INTEGER_EXT, chr(obj)]
            elif -2147483648 <= obj <= 2147483647:
                bytes += [INTEGER_EXT, struct.pack(">l", obj)]
            else:
                sign = chr(obj < 0)
                obj = abs(obj)

                big_bytes = []
                while obj > 0:
                    big_bytes.append(chr(obj & 0xff))
                    obj >>= 8

                if len(big_bytes) < 256:
                    bytes += [SMALL_BIG_EXT, chr(len(big_bytes)), sign] + big_bytes
                else:
                    bytes += [LARGE_BIG_EXT, struct.pack(">L", len(big_bytes)), sign] + big_bytes
        elif isinstance(obj, float):
            floatstr = "%.20e" % obj
            bytes += [FLOAT_EXT, floatstr + "\x00"*(31-len(floatstr))]
        elif isinstance(obj, Atom):
            bytes += [ATOM_EXT, struct.pack(">H", len(obj)), obj]
        elif isinstance(obj, str):
            bytes += [BINARY_EXT, struct.pack(">L", len(obj)), obj]
        elif isinstance(obj, unicode):
            bytes += self.encode_unicode(obj)
            if not self.encoding:
                self._encode([ord(x) for x in obj], bytes)
            else:
                st = obj.encode(self.encoding)
                if self.unicode_type == "binary":
                    bytes += [BINARY_EXT, struct.pack(">L", len(st)), st]
                elif self.unicode_type == "str":
                    bytes += [STRING_EXT, struct.pack(">H", len(st)), st]
                else:
                    raise TypeError("Unknown unicode encoding type %s" % self.unicode_type)
        elif isinstance(obj, tuple):
            n = len(obj)
            if n < 256:
                bytes += [SMALL_TUPLE_EXT, chr(n)]
            else:
                bytes += [LARGE_TUPLE_EXT, struct.pack(">L", n)]
            for item in obj:
                self._encode(item, bytes)
        elif obj == []:
            bytes.append(NIL_EXT)
        elif isinstance(obj, list):
            bytes += [LIST_EXT, struct.pack(">L", len(obj))]
            for item in obj:
                self._encode(item, bytes)
            bytes.append(NIL_EXT) # list tail - no such thing in Python
        elif isinstance(obj, Reference):
            bytes += [NEW_REFERENCE_EXT,
                struct.pack(">H", len(obj.ref_id)),
                ATOM_EXT, struct.pack(">H", len(obj.node)), obj.node,
                chr(obj.creation), struct.pack(">%dL" % len(obj.ref_id), *obj.ref_id)]
        elif isinstance(obj, Port):
            bytes += [PORT_EXT,
                ATOM_EXT, struct.pack(">H", len(obj.node)), obj.node,
                struct.pack(">LB", obj.port_id, obj.creation)]
        elif isinstance(obj, PID):
            bytes += [PID_EXT,
                ATOM_EXT, struct.pack(">H", len(obj.node)), obj.node,
                struct.pack(">LLB", obj.pid_id, obj.serial, obj.creation)]
        elif isinstance(obj, Export):
            bytes += [EXPORT_EXT,
                ATOM_EXT, struct.pack(">H", len(obj.module)), obj.module,
                ATOM_EXT, struct.pack(">H", len(obj.function)), obj.function,
                SMALL_INTEGER_EXT, chr(obj.arity)]
        else:
            raise NotImplementedError("Unable to serialize %r" % obj)
