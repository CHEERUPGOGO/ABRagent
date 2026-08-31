# -*- coding: utf-8 -*-
"""utils"""

def fix_json_escape(json_str):
    out = []; i = 0
    while i < len(json_str):
        if json_str[i] == chr(34):
            out.append(chr(34)); i += 1
            while i < len(json_str):
                if json_str[i] == chr(34): out.append(chr(34)); i += 1; break
                elif json_str[i] == chr(92):
                    if i+1 < len(json_str) and json_str[i+1] in chr(34)+chr(92)+chr(92)+"/bfnrtu":
                        out.append(json_str[i]); out.append(json_str[i+1]); i += 2
                    else: out.append(chr(92)+chr(92)); i += 1
                elif json_str[i] in chr(10)+chr(13)+chr(9):
                    out.append({chr(10): chr(92)+"n", chr(13): chr(92)+"r", chr(9): chr(92)+"t"}.get(json_str[i], " ")); i += 1
                else: out.append(json_str[i]); i += 1
        else: out.append(json_str[i]); i += 1
    return "".join(out)
