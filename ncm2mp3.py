#!/usr/bin/env python3
"""
NCM to MP3 Converter
支持 CTENFDNT 和 CTENFDAM 两种 NCM 格式
"""

import os
import sys
import json
import struct
import shutil
import argparse
from pathlib import Path
from Crypto.Cipher import AES

def decrypt_ncm(file_path: str) -> tuple:
    """
    解密 .ncm 文件，返回 (音频数据, 原始格式扩展名, 元数据)
    支持头部: CTENFDNT (PC端) 和 CTENFDAM (移动端/新版)
    """
    with open(file_path, 'rb') as f:
        # 1. 读取并验证文件头
        header = f.read(8)
        if header not in [b'CTENFDNT', b'CTENFDAM']:
            raise ValueError(f"Unsupported NCM format. Header: {header[:4]}")

        # 2. 读取密钥块
        key_length = struct.unpack('<I', f.read(4))[0]
        encrypted_key = f.read(key_length)
        f.read(5)  # 跳过 0x64 0x63 0x6D 0x61 0x67 (dcmag)

        # 3. 读取元数据块
        meta_length = struct.unpack('<I', f.read(4))[0]
        encrypted_meta = f.read(meta_length)
        f.read(5)  # 跳过 dcmag

        # 4. 解密密钥 (AES-128 ECB)
        core_key = b'neteasecloudmusic'[:16]
        aes = AES.new(core_key, AES.MODE_ECB)
        
        # 解密密钥
        decrypted_key = aes.decrypt(encrypted_key)
        real_key = decrypted_key[:16]

        # 解密元数据
        decrypted_meta = aes.decrypt(encrypted_meta)
        # 去除 PKCS7 填充
        pad_len = decrypted_meta[-1]
        if pad_len < 16:
            decrypted_meta = decrypted_meta[:-pad_len]
        # 跳过前22字节的标记
        meta_json = decrypted_meta[22:].decode('utf-8', errors='ignore')
        meta = json.loads(meta_json)
        
        # 提取格式
        format_ext = '.' + meta.get('format', 'mp3')
        if format_ext not in ['.mp3', '.flac']:
            format_ext = '.mp3'

        # 5. 读取 CRC 块 (校验用，可跳过)
        crc_length = struct.unpack('<I', f.read(4))[0]
        f.read(crc_length + 5)

        # 6. 读取并解密音频数据
        encrypted_audio = f.read()
        audio_data = b''
        for i in range(0, len(encrypted_audio), 16):
            block = encrypted_audio[i:i+16]
            if len(block) == 16:
                audio_data += aes.decrypt(block)
            else:
                # 最后一个不满16字节的块直接保留
                audio_data += block

    return audio_data, format_ext, meta

def flac_to_mp3(flac_data: bytes, output_mp3_path: str, ffmpeg_path: str = None) -> bool:
    """使用 ffmpeg 将 FLAC 数据转换为 MP3 文件"""
    temp_flac = output_mp3_path + ".temp.flac"
    with open(temp_flac, "wb") as f:
        f.write(flac_data)

    # 查找 ffmpeg
    if ffmpeg_path is None:
        ffmpeg_cmd = "ffmpeg"
    else:
        ffmpeg_cmd = str(ffmpeg_path)
    
    cmd = f'"{ffmpeg_cmd}" -i "{temp_flac}" -acodec libmp3lame -ab 320k "{output_mp3_path}" -y'
    ret = os.system(cmd)
    os.remove(temp_flac)
    
    if ret != 0:
        print(f"FFmpeg 转换失败，请确认已安装 FFmpeg")
        return False
    return True

def convert_ncm_to_mp3(input_path: str, output_dir: str = None, to_mp3: bool = True, keep_original: bool = False):
    """转换单个 ncm 文件"""
    input_path = Path(input_path)
    if not input_path.exists():
        print(f"文件不存在: {input_path}")
        return

    if output_dir is None:
        output_dir = input_path.parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    stem = input_path.stem
    print(f"正在处理: {input_path.name}")

    try:
        audio_data, fmt_ext, meta = decrypt_ncm(str(input_path))
        print(f"  格式: {fmt_ext}, 标题: {meta.get('title', '未知')}")
    except Exception as e:
        print(f"解密失败: {e}")
        return

    # 输出临时文件（原始格式）
    temp_output = output_dir / f"{stem}{fmt_ext}"
    with open(temp_output, "wb") as f:
        f.write(audio_data)

    final_path = None
    if fmt_ext == '.mp3' or not to_mp3:
        final_path = output_dir / f"{stem}{fmt_ext}"
        if temp_output != final_path:
            shutil.move(str(temp_output), str(final_path))
        print(f"已保存: {final_path}")
    elif fmt_ext == '.flac' and to_mp3:
        mp3_path = output_dir / f"{stem}.mp3"
        # 尝试查找同目录下的 ffmpeg.exe
        ffmpeg_exe = Path(sys.argv[0]).parent / "ffmpeg.exe"
        ffmpeg_path = str(ffmpeg_exe) if ffmpeg_exe.exists() else None
        
        if flac_to_mp3(audio_data, str(mp3_path), ffmpeg_path):
            print(f"已转换为 MP3: {mp3_path}")
            final_path = mp3_path
            if not keep_original:
                temp_output.unlink()
        else:
            print(f"转换失败，保留 FLAC 文件: {temp_output}")
            final_path = temp_output
    else:
        final_path = temp_output
        print(f"未知格式，已保存为: {final_path}")

    # 尝试写入 ID3 标签
    if final_path and final_path.exists():
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(final_path)
            if audio:
                if 'title' in meta:
                    audio['title'] = meta['title']
                if 'artist' in meta:
                    audio['artist'] = meta['artist']
                if 'album' in meta:
                    audio['album'] = meta['album']
                audio.save()
                print(f"已写入标签: {meta.get('title', '')} - {meta.get('artist', '')}")
        except Exception as e:
            print(f"写入标签失败: {e}")

def batch_convert(input_path: str, output_dir: str = None, recursive: bool = False, to_mp3: bool = True):
    """批量转换"""
    path = Path(input_path)
    if path.is_file() and path.suffix.lower() == '.ncm':
        convert_ncm_to_mp3(str(path), output_dir, to_mp3)
    elif path.is_dir():
        pattern = "**/*.ncm" if recursive else "*.ncm"
        ncm_files = list(path.glob(pattern))
        print(f"找到 {len(ncm_files)} 个 NCM 文件")
        for ncm_file in ncm_files:
            convert_ncm_to_mp3(str(ncm_file), output_dir, to_mp3)
    else:
        print("无效输入路径")

def main():
    parser = argparse.ArgumentParser(description="Convert NCM files to MP3 (支持 PC/移动端 NCM 格式)")
    parser.add_argument("input", help="输入文件或文件夹路径")
    parser.add_argument("-o", "--output", help="输出目录 (默认与输入相同)")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归搜索子文件夹")
    parser.add_argument("--no-mp3", action="store_true", help="不强制转为MP3，保留原始格式")
    parser.add_argument("--keep-flac", action="store_true", help="当转MP3时保留中间FLAC文件")
    args = parser.parse_args()

    batch_convert(args.input, args.output, args.recursive, to_mp3=not args.no_mp3)

if __name__ == "__main__":
    main()