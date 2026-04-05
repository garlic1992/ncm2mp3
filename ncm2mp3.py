#!/usr/bin/env python3
"""
NCM to MP3 Converter
将网易云音乐 .ncm 文件解密并转换为 .mp3 格式
依赖: pycryptodome, mutagen, (可选: ffmpeg 用于 FLAC→MP3)
"""

import os
import sys
import json
import base64
import struct
import shutil
import argparse
from pathlib import Path
from Crypto.Cipher import AES
from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3

# ---------- 解密核心 ----------
def generate_aes_key(key: bytes, transform: bytes) -> bytes:
    """自定义密钥扩展算法 (参考现有实现)"""
    key_bytes = key + b'\x00' * (16 - len(key))
    for i, t in enumerate(transform):
        key_bytes[i % 16] = key_bytes[i % 16] ^ t
    return key_bytes

def decrypt_ncm(file_path: str) -> tuple:
    """
    解密 .ncm 文件，返回 (音频数据, 原始格式扩展名, 元数据)
    格式扩展名: '.mp3' 或 '.flac'
    """
    with open(file_path, 'rb') as f:
        # 1. 验证文件头
        header = f.read(8)
        if header != b'CTENFDNT':
            raise ValueError("Not a valid NCM file")

        # 2. 读取密钥块
        key_length = struct.unpack('<I', f.read(4))[0]
        encrypted_key = f.read(key_length)
        f.read(5)  # 跳过 0x64 0x63 0x6D 0x61 0x67  (dcmag)
        # 解密密钥 (AES-128 ECB, 密钥: 0x233C6D8BDB1B... 实际用固定值)
        core_key = b'neteasecloudmusic'[:16]  # 标准密钥
        aes = AES.new(core_key, AES.MODE_ECB)
        decrypted_key = aes.decrypt(encrypted_key)
        # 实际密钥在前16字节
        real_key = decrypted_key[:16]

        # 3. 读取元数据块
        meta_length = struct.unpack('<I', f.read(4))[0]
        encrypted_meta = f.read(meta_length)
        f.read(5)  # 跳过 dcmag
        aes_meta = AES.new(core_key, AES.MODE_ECB)
        decrypted_meta = aes_meta.decrypt(encrypted_meta)
        # 去除 PKCS7 填充
        decrypted_meta = decrypted_meta[:-decrypted_meta[-1]]
        meta_json = decrypted_meta[22:]  # 跳过前22字节的标记
        meta = json.loads(meta_json.decode('utf-8'))
        # 提取格式
        format_ext = '.' + meta.get('format', 'mp3')
        if format_ext not in ['.mp3', '.flac']:
            format_ext = '.mp3'  # fallback

        # 4. 读取 CRC 块 (校验用，可跳过)
        crc_length = struct.unpack('<I', f.read(4))[0]
        f.read(crc_length + 5)

        # 5. 读取加密的音频数据
        encrypted_audio = f.read()
        # 使用 real_key 解密音频 (AES-128 ECB)
        aes_audio = AES.new(real_key, AES.MODE_ECB)
        # 音频数据长度可能不是16倍数，需要分块
        decrypted_audio = b''
        for i in range(0, len(encrypted_audio), 16):
            block = encrypted_audio[i:i+16]
            if len(block) == 16:
                decrypted_audio += aes_audio.decrypt(block)
            else:
                # 最后一个不满16字节的块直接保留（真实音频末尾可能无填充）
                decrypted_audio += block
        # 去除可能的填充 (PKCS7) – 音频数据不一定是完整的填充块，保守处理
        # 尝试去除最后一个字节数量的填充（如果最后字节 < 16 且所有最后字节值相同）
        # 此处简化：不自动去除，音频播放器通常能处理

    return decrypted_audio, format_ext, meta

# ---------- FLAC 转 MP3 (需要 FFmpeg) ----------
def flac_to_mp3(flac_data: bytes, output_mp3_path: str) -> bool:
    """使用 ffmpeg 将 FLAC 数据转换为 MP3 文件"""
    temp_flac = output_mp3_path + ".temp.flac"
    with open(temp_flac, "wb") as f:
        f.write(flac_data)

    # 调用 ffmpeg
    cmd = f'ffmpeg -i "{temp_flac}" -acodec libmp3lame -ab 320k "{output_mp3_path}" -y'
    ret = os.system(cmd)
    os.remove(temp_flac)
    if ret != 0:
        print(f"FFmpeg 转换失败，请确认已安装 FFmpeg 并加入 PATH")
        return False
    return True

# ---------- 主转换逻辑 ----------
def convert_ncm_to_mp3(input_path: str, output_dir: str = None, to_mp3: bool = True, keep_original: bool = False):
    """
    转换单个 ncm 文件
    :param to_mp3: 强制输出为 MP3（若原始为 FLAC 则转码，原始为 MP3 直接改名）
    :param keep_original: 是否保留原始格式文件（当 to_mp3=True 且原始为 FLAC 时，保留中间 FLAC）
    """
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
    except Exception as e:
        print(f"解密失败: {e}")
        return

    # 输出临时文件（原始格式）
    temp_output = output_dir / f"{stem}{fmt_ext}"
    with open(temp_output, "wb") as f:
        f.write(audio_data)

    final_path = None
    if fmt_ext == '.mp3' or not to_mp3:
        # 已经是 MP3 或不需要转 MP3
        final_path = output_dir / f"{stem}{fmt_ext}"
        if temp_output != final_path:
            shutil.move(str(temp_output), str(final_path))
        print(f"已保存: {final_path}")
    elif fmt_ext == '.flac' and to_mp3:
        # 需要转 MP3
        mp3_path = output_dir / f"{stem}.mp3"
        if flac_to_mp3(audio_data, str(mp3_path)):
            print(f"已转换为 MP3: {mp3_path}")
            final_path = mp3_path
            if not keep_original:
                temp_output.unlink()
        else:
            print(f"转换失败，保留 FLAC 文件: {temp_output}")
            final_path = temp_output
    else:
        # 其他格式（很少见）直接保存
        final_path = temp_output
        print(f"未知格式，已保存为: {final_path}")

    # 尝试写入 ID3 标签（从元数据）
    if final_path and final_path.exists():
        try:
            audio = MutagenFile(final_path)
            if audio:
                if 'title' in meta:
                    audio['title'] = meta['title']
                if 'artist' in meta:
                    audio['artist'] = meta['artist']
                if 'album' in meta:
                    audio['album'] = meta['album']
                if 'bitrate' in meta:
                    pass  # 无需写入
                audio.save()
                print(f"已写入标签: {meta.get('title', '')} - {meta.get('artist', '')}")
        except Exception as e:
            print(f"写入标签失败: {e}")

# ---------- 批量处理 ----------
def batch_convert(input_path: str, output_dir: str = None, recursive: bool = False, to_mp3: bool = True):
    path = Path(input_path)
    if path.is_file() and path.suffix.lower() == '.ncm':
        convert_ncm_to_mp3(str(path), output_dir, to_mp3)
    elif path.is_dir():
        pattern = "**/*.ncm" if recursive else "*.ncm"
        for ncm_file in path.glob(pattern):
            convert_ncm_to_mp3(str(ncm_file), output_dir, to_mp3)
    else:
        print("无效输入路径")

# ---------- 命令行入口 ----------
def main():
    parser = argparse.ArgumentParser(description="Convert NCM files to MP3")
    parser.add_argument("input", help="输入文件或文件夹路径")
    parser.add_argument("-o", "--output", help="输出目录 (默认与输入相同)")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归搜索子文件夹")
    parser.add_argument("--no-mp3", action="store_true", help="不强制转为MP3，保留原始格式")
    parser.add_argument("--keep-flac", action="store_true", help="当转MP3时保留中间FLAC文件")
    args = parser.parse_args()

    batch_convert(args.input, args.output, args.recursive, to_mp3=not args.no_mp3)

if __name__ == "__main__":
    main()