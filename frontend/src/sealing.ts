/**
 * Sealing and opening doctor's notes, entirely in this browser.
 *
 * The server stores what this module produces and never sees a passphrase or a
 * note's words. The scheme is the one `agent/sealed.py` writes into every
 * compartment in words, so the folder can be opened without this app:
 *
 *   key  = PBKDF2-HMAC-SHA256(passphrase as UTF-8, salt, iterations) → 32 bytes
 *   note = AES-256-GCM(key, 12-byte iv, plaintext, additional data = compartment id)
 *
 * The key is created non-extractable and lives only in the page's memory while
 * the notes are open. Nothing here touches localStorage or IndexedDB: an
 * unlocked key written to the browser's storage would outlive the consultation.
 */

import type { DoctorNoteSealed, NotesCompartment } from "./types";

export const ITERATIONS = 600_000;
export const CHECK_PLAINTEXT = "sealed doctor notes, check 1";

export interface NoteContent {
  author: string;
  /** The day the doctor says the note is about, as YYYY-MM-DD. */
  written: string;
  text: string;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function toB64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromB64(text: string): Uint8Array<ArrayBuffer> {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

function random(length: number): Uint8Array<ArrayBuffer> {
  return crypto.getRandomValues(new Uint8Array(length));
}

async function derive(passphrase: string, salt: Uint8Array<ArrayBuffer>, iterations: number) {
  const material = await crypto.subtle.importKey(
    "raw",
    encoder.encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function seal(key: CryptoKey, compartment: string, plaintext: string) {
  const iv = random(12);
  const sealed = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: encoder.encode(compartment) },
    key,
    encoder.encode(plaintext),
  );
  return { iv: toB64(iv), ciphertext: toB64(new Uint8Array(sealed)) };
}

/** The plaintext, or null when this key does not open it. */
async function open(
  key: CryptoKey,
  compartment: string,
  box: { iv: string; ciphertext: string },
): Promise<string | null> {
  try {
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: fromB64(box.iv), additionalData: encoder.encode(compartment) },
      key,
      fromB64(box.ciphertext),
    );
    return decoder.decode(plain);
  } catch {
    return null;
  }
}

/** A new compartment's body for the server, and the key it was sealed with. */
export async function createCompartment(passphrase: string) {
  const salt = random(16);
  const id = `cmp-${Array.from(random(16), (b) => b.toString(16).padStart(2, "0")).join("")}`;
  const key = await derive(passphrase, salt, ITERATIONS);
  const check = await seal(key, id, CHECK_PLAINTEXT);
  return {
    key,
    body: {
      id,
      kdf: { name: "PBKDF2", hash: "SHA-256", iterations: ITERATIONS, salt: toB64(salt) },
      check,
    },
  };
}

/** The key for *compartment*, or null when the passphrase does not open it. */
export async function unlock(
  compartment: NotesCompartment,
  passphrase: string,
): Promise<CryptoKey | null> {
  const key = await derive(passphrase, fromB64(compartment.kdf.salt), compartment.kdf.iterations);
  const check = await open(key, compartment.id, compartment.check);
  return check === CHECK_PLAINTEXT ? key : null;
}

export async function sealNote(key: CryptoKey, compartment: string, content: NoteContent) {
  return { compartment, ...(await seal(key, compartment, JSON.stringify(content))) };
}

/** The note's content, or null when it cannot be opened or is not a note. */
export async function openNote(key: CryptoKey, note: DoctorNoteSealed): Promise<NoteContent | null> {
  const plain = await open(key, note.compartment, note);
  if (plain === null) return null;
  try {
    const parsed = JSON.parse(plain) as Partial<NoteContent>;
    return {
      author: typeof parsed.author === "string" ? parsed.author : "",
      written: typeof parsed.written === "string" ? parsed.written : "",
      text: typeof parsed.text === "string" ? parsed.text : "",
    };
  } catch {
    return null;
  }
}
