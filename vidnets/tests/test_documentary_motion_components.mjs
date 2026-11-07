import test from 'node:test';
import assert from 'node:assert/strict';
import {
  selectDocumentaryHeadline,
  cleanDocumentaryHighlight,
  sanitizeDocumentaryProps,
} from '../src/remotion/engine/TextSanitizer.js';

test('1. TextSanitizer: selectDocumentaryHeadline preserves approved contract text verbatim', () => {
  const contractProps = {
    editorial_contract: {
      display_text: 'OPERATION CHASTISE: 1943 BOMBER LOG',
      approved: true,
      version: 4,
    },
  };
  const headline = selectDocumentaryHeadline(contractProps, {}, 12);
  assert.equal(headline, 'OPERATION CHASTISE: 1943 BOMBER LOG');
});

test('2. TextSanitizer: protects currency, numbers, percentages, and proper names', () => {
  const candidate = {
    display_text: '$4.2B in Royal Bullion & 85% Reserves',
  };
  const result = selectDocumentaryHeadline(candidate, {}, 10);
  assert.equal(result, '$4.2B in Royal Bullion & 85% Reserves');
});

test('3. TextSanitizer: compactLegacyText strips punctuation safely without partial word cuts', () => {
  const rawText = '   The secretive subterranean vault contained bullion,   ';
  const cleaned = cleanDocumentaryHighlight(rawText, 5);
  assert.equal(cleaned, 'secretive subterranean vault contained bullion');
  assert.ok(!cleaned.endsWith(','));
  assert.ok(!cleaned.endsWith(' '));
});

test('4. TextSanitizer: sanitizeDocumentaryProps handles missing, empty, or partial inputs gracefully', () => {
  const emptyResult = sanitizeDocumentaryProps({}, {});
  assert.deepEqual(emptyResult, {});

  const nullResult = sanitizeDocumentaryProps(null, null);
  assert.deepEqual(nullResult, {});

  const contractProps = {
    editorial_contract: {
      display_text: 'ROYAL CIPHER EXPOSÉ',
      emphasis_phrase: 'EXPOSÉ',
      approved: true,
      version: 4,
    },
  };
  const sanitized = sanitizeDocumentaryProps(contractProps, {});
  assert.equal(sanitized.title, 'ROYAL CIPHER EXPOSÉ');
  assert.equal(sanitized.headline, 'ROYAL CIPHER EXPOSÉ');
  assert.equal(sanitized.display_text, 'ROYAL CIPHER EXPOSÉ');
  assert.equal(sanitized.emphasis_phrase, 'EXPOSÉ');
});

test('5. TextSanitizer: non-approved director scenes fallback gracefully without blank text cards', () => {
  const unapprovedDirectorScene = {
    director_treatment_enabled: true,
    narration: 'British railway engineers constructed the high-speed transit tunnel in 1994.',
    text: { overlay_policy: 'single_short_highlight' },
  };
  const rawProps = {
    title: 'British railway engineers constructed the high-speed transit tunnel in 1994.',
  };
  const sanitized = sanitizeDocumentaryProps(rawProps, unapprovedDirectorScene);
  assert.ok(sanitized.title, 'Should provide non-empty title');
  assert.ok(sanitized.headline, 'Should provide non-empty headline');
  assert.ok(sanitized.display_text, 'Should provide non-empty display_text');
  assert.ok(!sanitized.title.includes('  '));
});

test('6. TextSanitizer: selectDocumentaryHeadline falls through candidates in deterministic order', () => {
  // Test _contract_text precedence over title and narration
  const propsWithMultiple = {
    title: 'Title Candidate',
    text: 'Text Candidate',
    _contract_text: 'Contract Text Candidate',
  };
  const result = selectDocumentaryHeadline(propsWithMultiple, { narration: 'Narration Fallback' }, 10);
  assert.equal(result, 'Contract Text Candidate');

  // Test scene narration fallback when props are empty
  const emptyProps = {};
  const sceneNarration = { narration: 'Historic Concorde supersonic flight touched down in London.' };
  const narrResult = selectDocumentaryHeadline(emptyProps, sceneNarration, 6);
  assert.ok(narrResult.includes('Historic Concorde supersonic flight'));
});

test('7. Component Registry & Director Mappings: specialist components are fully registered with aliases', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const { fileURLToPath } = await import('node:url');

  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const sceneRegistryPath = path.resolve(__dirname, '../src/remotion/engine/SceneRegistry.js');
  const sceneRegistrySrc = fs.readFileSync(sceneRegistryPath, 'utf8');

  // Verify component imports & exports in REGISTRY
  const requiredComponents = [
    'DeclassifiedDocument',
    'IsometricMap',
    'EvidencePinboard',
    'TechnicalBlueprint',
    'DepthTypography',
  ];
  for (const comp of requiredComponents) {
    assert.ok(
      sceneRegistrySrc.includes(comp),
      `SceneRegistry.js must register ${comp}`
    );
  }

  // Verify critical template aliases
  const expectedAliases = {
    'declassified-document': 'DeclassifiedDocument',
    'declassified-dossier': 'DeclassifiedDocument',
    'redaction-reveal': 'DeclassifiedDocument',
    'isometric-map': 'IsometricMap',
    'flight-tracker': 'IsometricMap',
    'evidence-pinboard': 'EvidencePinboard',
    'evidence-board': 'EvidencePinboard',
    'photo-pinboard': 'EvidencePinboard',
    'technical-blueprint': 'TechnicalBlueprint',
    'blueprint-reveal': 'TechnicalBlueprint',
    'cad-blueprint': 'TechnicalBlueprint',
    'depth-typography': 'DepthTypography',
    'behind-subject-text': 'DepthTypography',
  };

  for (const [alias, target] of Object.entries(expectedAliases)) {
    const aliasRegex = new RegExp(`['"]${alias}['"]\\s*:\\s*['"]${target}['"]`);
    assert.ok(
      aliasRegex.test(sceneRegistrySrc),
      `SceneRegistry.js must map alias '${alias}' to '${target}'`
    );
  }
});

test('8. DocumentaryComposition: DIRECTOR_GRAPHIC_TYPES aligns with specialist components and prevents dropping', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');
  const { fileURLToPath } = await import('node:url');

  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const docCompPath = path.resolve(__dirname, '../src/remotion/DocumentaryComposition.jsx');
  const docCompSrc = fs.readFileSync(docCompPath, 'utf8');

  // Check DIRECTOR_GRAPHIC_TYPES mappings
  const requiredMappings = [
    'declassified-document',
    'isometric-map',
    'evidence-pinboard',
    'technical-blueprint',
    'depth-typography',
  ];
  for (const mapping of requiredMappings) {
    assert.ok(
      docCompSrc.includes(`'${mapping}'`),
      `DocumentaryComposition.jsx must include '${mapping}' in DIRECTOR_GRAPHIC_TYPES`
    );
  }

  // Check directorGraphicEligible inclusion
  assert.ok(
    docCompSrc.includes("['DeclassifiedDocument', 'IsometricMap', 'EvidencePinboard', 'TechnicalBlueprint', 'DepthTypography'].includes(directorGraphicType)"),
    'DocumentaryComposition.jsx must include specialist components in directorGraphicEligible guard'
  );

  // Check mapOwnsCanvas inclusion
  assert.ok(
    docCompSrc.includes("'IsometricMap'"),
    'DocumentaryComposition.jsx must include IsometricMap in mapOwnsCanvas'
  );
});
