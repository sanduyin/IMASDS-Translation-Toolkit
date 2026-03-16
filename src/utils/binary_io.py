# src/utils/binary_io.py
import struct
import io

def read_uint32(data_or_file, offset=None):
    """通用读取 4字节无符号整数"""
    if hasattr(data_or_file, 'read'):
        if offset is not None: data_or_file.seek(offset)
        data = data_or_file.read(4)
        if len(data) < 4: return 0
        return struct.unpack('<I', data)[0]
    else:
        return struct.unpack('<I', data_or_file[offset:offset + 4])[0]

def read_uint16(data_or_file, offset=None):
    """通用读取 2字节无符号整数"""
    if hasattr(data_or_file, 'read'):
        if offset is not None: data_or_file.seek(offset)
        data = data_or_file.read(2)
        if len(data) < 2: return 0
        return struct.unpack('<H', data)[0]
    else:
        return struct.unpack('<H', data_or_file[offset:offset + 2])[0]

def read_string_bytes(f, offset):
    """从二进制文件中读取遇到 0x00 结束的字符串"""
    pos = f.tell()
    f.seek(offset)
    byte_list =[]
    while True:
        b = f.read(1)
        if b == b'\x00' or not b: break
        byte_list.append(b)
    f.seek(pos)
    return b"".join(byte_list)

def nlzss_compress(input_bytes):
    """NDS LZ10 压缩算法 (提取自 5.Repack_ROM_v22.py)"""
    if len(input_bytes) == 0: return b""
    out = io.BytesIO()
    out.write(struct.pack("<I", (len(input_bytes) << 8) | 0x10))
    
    class NLZ10Window:
        size, match_min, match_max = 4096, 3, 18
        def __init__(self, buf):
            self.data = buf
            self.hash = {}
            self.index = 0
            
        def search(self):
            if self.index >= len(self.data): return None
            counts =[]
            curr = self.data[self.index]
            if curr in self.hash:
                for pos in reversed(self.hash[curr]):
                    if self.index - pos >= self.size: break
                    match_len = 0
                    for i in range(min(len(self.data)-self.index, self.match_max)):
                        if self.data[pos+i] == self.data[self.index+i]: match_len += 1
                        else: break
                    if match_len >= self.match_min:
                        counts.append((match_len, -(self.index - pos)))
                        if match_len >= self.match_max: break
            if curr not in self.hash: self.hash[curr] = []
            self.hash[curr].append(self.index)
            return max(counts, key=lambda x:x[0]) if counts else None

    window = NLZ10Window(input_bytes)
    tokens =[]
    while window.index < len(input_bytes):
        match = window.search()
        if match:
            tokens.append(match)
            window.index += match[0]
        else:
            tokens.append(input_bytes[window.index])
            window.index += 1
            
    def pack_chunk(chunk):
        flag = 0
        data = bytearray()
        for i, token in enumerate(chunk):
            if isinstance(token, tuple):
                flag |= (1 << (7-i))
                l, d = token
                val = ((l-3) << 12) | ((-d-1) & 0xFFF)
                data.extend(struct.pack(">H", val))
            else:
                data.append(token)
        return struct.pack("B", flag) + data

    for i in range(0, len(tokens), 8):
        out.write(pack_chunk(tokens[i:i+8]))
    
    padding = (4 - (out.tell() % 4)) % 4
    if padding: out.write(b'\x00' * padding)
    return out.getvalue()