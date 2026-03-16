# src/utils/bbq_format.py
import struct
import os
from src.utils.binary_io import read_uint32, read_string_bytes

def parse_bbq_file(file_path, is_scn=True):
    """
    解析游戏的 .bbq 封包文件格式。
    增加 is_scn 开关：如果是 TBL，强行跳过 Section 5 角色解析，避免串号乱码。
    """
    with open(file_path, 'rb') as f:
        header_sig = f.read(8)
        if header_sig[:4] != b'\x2E\x42\x42\x51':  # '.BBQ'
            return[] 

        f.read(8) 
        header_size = read_uint32(f)
        n_sections = read_uint32(f)
        
        if n_sections == 0 or n_sections > 200: 
            return[]
            
        if f.tell() != header_size: 
            f.seek(header_size)

        sections = {}
        for _ in range(n_sections):
            offset = f.tell()
            sect_id = read_uint32(f)
            values =[read_uint32(f) for _ in range(4)]
            sections[sect_id] = {'offset': offset, 'values': values}

        if 7 not in sections: 
            return[]
            
        sec7 = sections[7]
        ptr_table_offset = sec7['offset'] + sec7['values'][0]
        num_str = sec7['values'][1]
        pool_offset = sec7['offset'] + sec7['values'][2]

        f.seek(ptr_table_offset)
        pointers = [read_uint32(f) for _ in range(num_str)]
        
        raw_texts = []
        max_bytes_list = []
        text_offsets =[]
        pointer_locs =[]

        # 1. 无损读取所有文本
        for i in range(num_str):
            current_ptr_loc = ptr_table_offset + (i * 4)
            pointer_locs.append(current_ptr_loc)
            curr_addr = pool_offset + pointers[i]
            text_offsets.append(curr_addr)

            raw_bytes = read_string_bytes(f, curr_addr)
            try: 
                txt = raw_bytes.decode('cp932')
            except UnicodeDecodeError: 
                txt = f"<HEX:{raw_bytes.hex()}>"
            raw_texts.append(txt)

            if i < num_str - 1:
                gap = (pool_offset + pointers[i+1]) - curr_addr
                max_bytes_list.append(len(raw_bytes) if gap <= 0 else gap - 1)
            else: 
                max_bytes_list.append(len(raw_bytes))

        # 2. 仅当是 SCN 剧情文件时，才去解析 Section 5 角色映射表！
        speaker_map = {}
        type_map = {}
        if is_scn and 5 in sections:
            sec5 = sections[5]
            if sec5['values'][3] >= 16:
                num_views = sec5['values'][3] // 16
                view_base = sec5['offset'] + sec5['values'][2]
                f.seek(view_base)
                
                for _ in range(num_views):
                    f.read(4) 
                    idx_name, idx_l1, idx_l2 = struct.unpack('<iii', f.read(12))
                    
                    name_str = raw_texts[idx_name] if 0 <= idx_name < num_str else ""
                    if 0 <= idx_name < num_str: type_map[idx_name] = "角色名"
                    if 0 <= idx_l1 < num_str:
                        speaker_map[idx_l1] = name_str
                        type_map[idx_l1] = "对话1"
                    if 0 <= idx_l2 < num_str:
                        speaker_map[idx_l2] = name_str
                        type_map[idx_l2] = "对话2"

        # 3. 组装数据并过滤空行
        entries =[]
        for i in range(num_str):
            txt = raw_texts[i]
            max_bytes = max_bytes_list[i]
            
            # 过滤“幽灵空行”
            if max_bytes == 0 and not txt.strip():
                continue
                
            # 根据是否为 SCN 决定显示样式
            if is_scn:
                speaker_display = speaker_map.get(i, "System/TBL")
                type_display = type_map.get(i, "脚本/其他")
            else:
                speaker_display = "×"  # TBL 专属：填个×
                type_display = "系统文本"

            entries.append({
                'Original_Text': txt,
                'Speaker': speaker_display,
                'Translated_Text': "", 
                'File': os.path.basename(file_path),
                'Text_Offset': f"0x{text_offsets[i]:X}",
                'Pointer_Locs': f"0x{pointer_locs[i]:X}",
                'Max_Bytes': max_bytes,
                'Index': i,
                'Type': type_display
            })
            
        return entries