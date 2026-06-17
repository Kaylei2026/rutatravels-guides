// translate-claude.js
// Translates Ruta Travels guide JSONs using Claude via the Railway proxy.
// Same whitelist as translate.js, but uses Claude instead of DeepL.
//
// Usage:
//   node translate-claude.js pt
//   node translate-claude.js it --file paris-3day-en.json
//   node translate-claude.js it --dry-run

const fs = require('fs');
const path = require('path');
const fetch = require('node-fetch');

// Railway proxy that fronts Anthropic API (same one the app uses for destination content)
const PROXY_URL = process.env.RUTA_PROXY_URL || 'https://ruta-travels-production.up.railway.app/api/claude';
const RUTA_API_KEY = process.env.RUTA_API_KEY;

if (!RUTA_API_KEY) {
  console.error('ERROR: RUTA_API_KEY env var required.');
  console.error('Get it from the .env file in your ruta-travels app folder, or your Railway dashboard.');
  process.exit(1);
}

const TARGET_LANGS = {
  pt: 'Brazilian Portuguese',
  es: 'Spanish',
  fr: 'French',
  de: 'German',
  it: 'Italian',
  ja: 'Japanese',
  zh: 'Mandarin Chinese (Simplified)',
  ar: 'Modern Standard Arabic',
};

// Same whitelist as translate.js
const TRANSLATABLE_FIELDS = new Set([
  'guide_description',
  'cluster_name',
  'cluster_tagline',
  'neighborhood_description',
  'notes',
]);

const args = process.argv.slice(2);
const targetLang = args[0];
const dryRun = args.includes('--dry-run');
const fileIdx = args.indexOf('--file');
const singleFile = fileIdx > -1 ? args[fileIdx + 1] : null;

if (!targetLang || !TARGET_LANGS[targetLang]) {
  console.error(`Usage: node translate-claude.js <lang> [--dry-run] [--file paris-3day-en.json]`);
  console.error(`Supported langs: ${Object.keys(TARGET_LANGS).join(', ')}`);
  process.exit(1);
}

const targetLangName = TARGET_LANGS[targetLang];

// Walks JSON, collecting translatable strings (same as DeepL version)
const collectTranslatable = (obj, path = '', collected = { strings: [], paths: [] }) => {
  if (obj == null) return collected;
  if (Array.isArray(obj)) {
    obj.forEach((item, i) => collectTranslatable(item, `${path}[${i}]`, collected));
    return collected;
  }
  if (typeof obj === 'object') {
    for (const [key, value] of Object.entries(obj)) {
      const newPath = path ? `${path}.${key}` : key;
      if (TRANSLATABLE_FIELDS.has(key) && typeof value === 'string' && value.trim()) {
        collected.strings.push(value);
        collected.paths.push(newPath);
      } else {
        collectTranslatable(value, newPath, collected);
      }
    }
  }
  return collected;
};

const setAtPath = (obj, pathStr, value) => {
  const parts = pathStr.replace(/\[(\d+)\]/g, '.$1').split('.');
  let curr = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    curr = curr[parts[i]];
  }
  curr[parts[parts.length - 1]] = value;
};

// Translate a batch of strings via Claude proxy
const translateBatch = async (strings, langName) => {
  if (strings.length === 0) return [];

  // Number the strings so Claude returns them in a parseable format
  const numbered = strings.map((s, i) => `[${i + 1}] ${s}`).join('\n\n');

  const prompt = `Translate the following travel guide passages into ${langName}.

Rules:
- Output ONLY the translations, no preamble or markdown
- Preserve proper nouns: place names, landmark names, building names, neighborhood names stay in their original language
- Preserve specific dates, years, prices, distances, measurements
- Preserve URLs and identifiers verbatim
- Number each translation matching the input number, format: [N] <translation>
- Each translation on its own line, separated by blank lines, matching input structure
- Maintain a natural travel-guide tone, not a literal word-by-word translation
- Do not add or remove information
- Never use em dashes (the long dash) or en dashes; where a dash is needed use a comma, a colon, or a spaced hyphen ( - ) instead

Passages to translate (${strings.length} total):

${numbered}`;

  const res = await fetch(PROXY_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': RUTA_API_KEY,
    },
    body: JSON.stringify({ prompt, max_tokens: 4096 }),
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`Proxy ${res.status}: ${errText}`);
  }

  const data = await res.json();
  const text = data.text || '';

  // Parse numbered output. Match [N] at start of line, capture everything until
  // the next [N] marker or end of text. More forgiving regex than before.
  const result = new Array(strings.length).fill(null);
  const markerPattern = /^\s*\[(\d+)\]\s*/gm;
  const markers = [];
  let m;
  while ((m = markerPattern.exec(text)) !== null) {
    markers.push({ idx: parseInt(m[1], 10) - 1, start: m.index, contentStart: m.index + m[0].length });
  }
  for (let i = 0; i < markers.length; i++) {
    const cur = markers[i];
    const next = markers[i + 1];
    const content = text.slice(cur.contentStart, next ? next.start : text.length).trim();
    if (cur.idx >= 0 && cur.idx < strings.length) {
      result[cur.idx] = content;
    }
  }

  // Check for missing translations
  const missing = result.map((v, i) => v == null ? i + 1 : null).filter(v => v != null);
  if (missing.length > 0) {
    console.log('--- DEBUG: Claude response ---');
    console.log(text.slice(0, 500));
    console.log('... (truncated) ...');
    console.log(text.slice(-500));
    console.log('--- END DEBUG ---');
    throw new Error(`Missing translations for items: ${missing.join(', ')}`);
  }

  return result;
};

const processFile = async (filename) => {
  const inputPath = path.join(__dirname, filename);
  const outputName = filename.replace('-en.json', `-${targetLang}.json`);
  const outputPath = path.join(__dirname, outputName);

  const src = fs.readFileSync(inputPath, 'utf8');
  const data = JSON.parse(src);

  const { strings, paths } = collectTranslatable(data);
  console.log(`  ${filename}: ${strings.length} strings`);

  if (dryRun) {
    paths.forEach((p, i) => {
      console.log(`    [${p}]`);
      console.log(`      "${strings[i].slice(0, 80)}${strings[i].length > 80 ? '...' : ''}"`);
    });
    return;
  }

  // Claude can handle ~30 strings per request reliably without losing track
  const BATCH_SIZE = 4;
  const translated = [];
  for (let i = 0; i < strings.length; i += BATCH_SIZE) {
    const batch = strings.slice(i, i + BATCH_SIZE);
    const result = await translateBatch(batch, targetLangName);
    translated.push(...result);
  }

  const output = JSON.parse(JSON.stringify(data));
  paths.forEach((p, i) => setAtPath(output, p, translated[i]));

  fs.writeFileSync(outputPath, JSON.stringify(output, null, 2));
  console.log(`  -> ${outputName}`);
};

const main = async () => {
  const allFiles = fs.readdirSync(__dirname).filter(f => f.endsWith('-en.json'));
  const files = singleFile ? [singleFile] : allFiles;

  if (files.length === 0) {
    console.error('No -en.json files found.');
    process.exit(1);
  }

  console.log(`Translating ${files.length} file(s) to ${targetLangName} via Claude${dryRun ? ' (DRY RUN)' : ''}...`);
  console.log('');

  for (const file of files) {
    try {
      await processFile(file);
    } catch (e) {
      console.error(`  ERROR processing ${file}: ${e.message}`);
    }
  }

  console.log('');
  console.log('Done.');
};

main().catch(e => {
  console.error('FATAL:', e);
  process.exit(1);
});
