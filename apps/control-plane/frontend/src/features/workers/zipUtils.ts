/**
 * ZIP utilities for the worker editor.
 *
 * Reading: uses fflate to unzip an uploaded package into a flat path→content
 * map (text files only; binary entries are skipped with a note).
 *
 * Writing: a hand-rolled STORE-mode zip writer (no compression) that produces
 * a valid zip blob from a flat list of {path, content} entries. Kept inline
 * rather than using fflate's zipSync so the existing CRC32/central-directory
 * implementation stays untouched and testable.
 */

import { unzipSync, strFromU8 } from "fflate";

export type ZipEntry = { path: string; content: string };

export type ParsedZip = {
  entries: ZipEntry[];
  skippedBinary: string[];
};

/**
 * Unzip an uploaded zip into text-file entries.
 *
 * Directories (entries ending with "/") are ignored — the tree builder
 * reconstructs the directory structure from file paths. Binary entries
 * (non-UTF-8 decodable) are collected in skippedBinary.
 */
export function parseZip(data: Uint8Array): ParsedZip {
  const unzipped = unzipSync(data);
  const entries: ZipEntry[] = [];
  const skippedBinary: string[] = [];

  for (const [path, bytes] of Object.entries(unzipped)) {
    // Skip directory markers.
    if (path.endsWith("/")) continue;
    try {
      const content = strFromU8(bytes);
      entries.push({ path, content });
    } catch {
      skippedBinary.push(path);
    }
  }

  // Sort entries by path for deterministic tree rendering.
  entries.sort((a, b) => a.path.localeCompare(b.path));
  return { entries, skippedBinary };
}

/**
 * Read a File (from <input type="file">) as a Uint8Array.
 */
export function readFileBytes(file: File): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

// ─── ZIP writer (STORE mode, no compression) ─────────────────────────────

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function writeUint16(target: number[], value: number) {
  target.push(value & 0xff, (value >>> 8) & 0xff);
}

function writeUint32(target: number[], value: number) {
  target.push(
    value & 0xff,
    (value >>> 8) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 24) & 0xff,
  );
}

function dosDateTime(date: Date) {
  const year = Math.max(date.getFullYear(), 1980);
  const dosTime =
    (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { dosDate, dosTime };
}

function bytesFromParts(parts: Array<number[] | Uint8Array>) {
  const size = parts.reduce((total, part) => total + part.length, 0);
  const result = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

export function buildZipBlob(entries: ZipEntry[]): Blob {
  const encoder = new TextEncoder();
  const now = dosDateTime(new Date());
  const localParts: Array<number[] | Uint8Array> = [];
  const centralParts: Array<number[] | Uint8Array> = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBytes = encoder.encode(entry.path);
    const contentBytes = encoder.encode(entry.content);
    const checksum = crc32(contentBytes);

    const localHeader: number[] = [];
    writeUint32(localHeader, 0x04034b50);
    writeUint16(localHeader, 20);
    writeUint16(localHeader, 0x0800);
    writeUint16(localHeader, 0);
    writeUint16(localHeader, now.dosTime);
    writeUint16(localHeader, now.dosDate);
    writeUint32(localHeader, checksum);
    writeUint32(localHeader, contentBytes.length);
    writeUint32(localHeader, contentBytes.length);
    writeUint16(localHeader, nameBytes.length);
    writeUint16(localHeader, 0);
    localParts.push(localHeader, nameBytes, contentBytes);

    const centralHeader: number[] = [];
    writeUint32(centralHeader, 0x02014b50);
    writeUint16(centralHeader, 20);
    writeUint16(centralHeader, 20);
    writeUint16(centralHeader, 0x0800);
    writeUint16(centralHeader, 0);
    writeUint16(centralHeader, now.dosTime);
    writeUint16(centralHeader, now.dosDate);
    writeUint32(centralHeader, checksum);
    writeUint32(centralHeader, contentBytes.length);
    writeUint32(centralHeader, contentBytes.length);
    writeUint16(centralHeader, nameBytes.length);
    writeUint16(centralHeader, 0);
    writeUint16(centralHeader, 0);
    writeUint16(centralHeader, 0);
    writeUint16(centralHeader, 0);
    writeUint32(centralHeader, 0);
    writeUint32(centralHeader, offset);
    centralParts.push(centralHeader, nameBytes);

    offset += localHeader.length + nameBytes.length + contentBytes.length;
  }

  const centralDirectory = bytesFromParts(centralParts);
  const endRecord: number[] = [];
  writeUint32(endRecord, 0x06054b50);
  writeUint16(endRecord, 0);
  writeUint16(endRecord, 0);
  writeUint16(endRecord, entries.length);
  writeUint16(endRecord, entries.length);
  writeUint32(endRecord, centralDirectory.length);
  writeUint32(endRecord, offset);
  writeUint16(endRecord, 0);

  const zipBytes = bytesFromParts([...localParts, centralDirectory, new Uint8Array(endRecord)]);
  return new Blob([zipBytes], { type: "application/zip" });
}
