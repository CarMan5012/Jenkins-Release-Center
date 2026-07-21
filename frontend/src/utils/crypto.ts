import JSEncrypt from 'jsencrypt';
import request from './request';

let cachedPublicKey: string | null = null;

/**
 * 异步获取后端的 RSA 公钥
 */
export async function getPublicKey(): Promise<string> {
  if (cachedPublicKey) return cachedPublicKey;
  const response = await request.get('/auth/public-key');
  cachedPublicKey = response.data.public_key;
  return cachedPublicKey!;
}

/**
 * 对明文数据进行 RSA 加密
 */
export async function encryptData(data: string): Promise<string> {
  if (!data) return '';
  const key = await getPublicKey();
  const encryptor = new JSEncrypt();
  encryptor.setPublicKey(key);
  const encrypted = encryptor.encrypt(data);
  if (!encrypted) {
    throw new Error('敏感数据安全加密失败，请稍后重试');
  }
  return encrypted;
}
