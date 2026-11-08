'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const jobRunner = require('../lib/jobRunner');
const scriptAdapter = require('../adapters/script');

test('jobSummary correctly parses and preserves duration, word counts, references, and output paths', () => {
  const mockJob = {
    jobId: 'test_job_123',
    title: 'The Secret Concorde Flight',
    description: 'An untold story about the supersonic airliner.',
    premise: 'In 1977, a classified flight tested new navigational telemetry.',
    thumbnailIdea: 'Concorde banking sharply at dusk with orange afterburners and bold text THE SECRET FLIGHT',
    channel: {
      id: 'c_aviation',
      name: 'Aviation Unveiled',
      niche: 'Aviation History',
      scriptSettings: {
        duration: '5min',
        targetMinutes: 5,
        customStructure: 'Hook -> Secret Program -> Test Run -> Crisis -> Legacy',
        tone: 'Dramatic, investigative, authoritative',
        rules: 'Verify all tail numbers and dates',
        referenceTranscript: 'Reference speaker intro: We were flying at Mach 2 above the clouds when...'
      }
    },
    targetDurationMinutes: 5,
    targetWordCount: 750,
    requirements: {
      minimumWords: 675,
      targetWords: 750,
      thumbnail: {
        status: 'required',
        aspect: '16:9',
        concept: 'Concorde banking sharply at dusk with orange afterburners',
        referenceUrl: 'https://youtu.be/test1234567',
        referenceImageUrl: 'https://i.ytimg.com/vi/test1234567/maxresdefault.jpg'
      }
    },
    titlePackage: {
      refThumb: 'https://youtu.be/test1234567',
      refThumbImageUrl: 'https://i.ytimg.com/vi/test1234567/maxresdefault.jpg',
      refScript: 'https://youtu.be/scriptref123',
      referenceTranscript: 'Reference speaker intro: We were flying at Mach 2 above the clouds when...'
    }
  };

  const summary = jobRunner.jobSummary(mockJob);

  assert.equal(summary.jobId, 'test_job_123');
  assert.equal(summary.title, 'The Secret Concorde Flight');
  assert.equal(summary.targetDurationMinutes, 5);
  assert.equal(summary.targetWordCount, 750);
  assert.equal(summary.minimumWords, 675);

  // Script style & reference check
  assert.equal(summary.scriptStyle.tone, 'Dramatic, investigative, authoritative');
  assert.equal(summary.scriptStyle.structure, 'Hook -> Secret Program -> Test Run -> Crisis -> Legacy');
  assert.equal(summary.scriptStyle.refScriptUrl, 'https://youtu.be/scriptref123');
  assert.equal(summary.scriptStyle.hasReferenceTranscript, true);
  assert.match(summary.scriptStyle.referenceTranscriptPreview, /Mach 2 above the clouds/);

  // Thumbnail style & reference image check
  assert.equal(summary.thumbnailStyle.aspect, '16:9');
  assert.equal(summary.thumbnailStyle.concept, 'Concorde banking sharply at dusk with orange afterburners and bold text THE SECRET FLIGHT');
  assert.equal(summary.thumbnailStyle.referenceImageUrl, 'https://i.ytimg.com/vi/test1234567/maxresdefault.jpg');
  assert.equal(summary.thumbnailStyle.referenceUrl, 'https://youtu.be/test1234567');

  // Output paths check
  assert.ok(summary.outputLocations.scriptPath.endsWith('script.txt'));
  assert.ok(summary.outputLocations.videoPath.endsWith('video.mp4'));
  assert.ok(summary.outputLocations.thumbnailPath.endsWith('thumbnail.jpg'));
});

test('synthesizeNarration dynamically uses title, premise, and niche rather than hardcoded railway switch text', () => {
  const aviationJob = {
    title: 'The Secret Concorde Flight',
    premise: 'In 1977, a classified flight tested new navigational telemetry.',
    targetDurationMinutes: 5,
    channel: {
      name: 'Aviation Unveiled',
      niche: 'Aviation History'
    }
  };

  const narration = scriptAdapter.synthesizeNarration(aviationJob);
  assert.match(narration, /The Secret Concorde Flight/i);
  assert.match(narration, /aviation history/i);
  assert.match(narration, /Aviation Unveiled/);
  assert.doesNotMatch(narration, /switch mechanism|turnout or points|railway locomotive/i);

  const wordCount = narration.split(/\s+/).filter(Boolean).length;
  assert.ok(wordCount >= scriptAdapter.minimumScriptWords(aviationJob), `Word count ${wordCount} must cover 5 minutes`);
});
