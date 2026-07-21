import sys
import os
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def main():
    if len(sys.argv) < 3:
        print("Usage: python decrypt_backup.py <backup_file.zip.enc> <key_file> [output_file.zip]")
        sys.exit(1)
        
    enc_path = sys.argv[1]
    key_path = sys.argv[2]
    
    if len(sys.argv) >= 4:
        out_path = sys.argv[3]
    else:
        out_path = enc_path.replace(".enc", "")
        if out_path == enc_path:
            out_path = enc_path + ".decrypted.zip"
            
    if not os.path.exists(enc_path):
        print(f"Error: Encrypted file {enc_path} not found.")
        sys.exit(1)
        
    if not os.path.exists(key_path):
        print(f"Error: Key file {key_path} not found.")
        sys.exit(1)
        
    try:
        with open(key_path, "r", encoding="utf-8") as kf:
            key_material = kf.read().strip()
    except Exception as e:
        print(f"Error reading key file: {str(e)}")
        sys.exit(1)
        
    if not key_material:
        print("Error: Key file is empty.")
        sys.exit(1)
        
    # Derive key using SHA-256
    aes_key = hashlib.sha256(key_material.encode()).digest()
    
    try:
        with open(enc_path, "rb") as f:
            file_content = f.read()
    except Exception as e:
        print(f"Error reading encrypted file: {str(e)}")
        sys.exit(1)
        
    if len(file_content) < 5 + 12 + 16:
        print("解密认证失败：备份密钥错误或文件已损坏")
        sys.exit(1)
        
    header = file_content[:5]
    if header != b"JRCB1":
        print("解密认证失败：备份密钥错误或文件已损坏")
        sys.exit(1)
        
    nonce = file_content[5:17]
    ciphertext = file_content[17:]
    
    try:
        aesgcm = AESGCM(aes_key)
        decrypted_data = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        print("解密认证失败：备份密钥错误或文件已损坏")
        sys.exit(1)
        
    try:
        with open(out_path, "wb") as f:
            f.write(decrypted_data)
    except Exception as e:
        print(f"Error writing decrypted file: {str(e)}")
        sys.exit(1)
        
    print(f"Success: Decrypted zip saved to {out_path}")

if __name__ == "__main__":
    main()
