import test from 'node:test';
import assert from 'node:assert/strict';
import { decodePcm, LiveAudio } from './liveAudio.js';

test('live PCM decoding respects little-endian signed samples', () => {
  const values = Buffer.alloc(8);
  [-32768, -1, 0, 32767].forEach((value, i) => values.writeInt16LE(value, i * 2));
  assert.deepEqual(Array.from(decodePcm(values.toString('base64'))), [-1, -1/32768, 0, 32767/32768]);
  assert.throws(() => decodePcm('AA=='));
});
test('interrupt stops all queued speech and resets the playback clock', () => {
  let stopped = 0, disconnected = 0;
  const audio = new LiveAudio(() => {}, () => {});
  audio.sources.add({stop(){stopped++;},disconnect(){disconnected++;}});
  audio.sources.add({stop(){stopped++;},disconnect(){disconnected++;}});
  audio.nextTime = 100;
  audio.interrupt();
  assert.equal(stopped, 2); assert.equal(disconnected, 2);
  assert.equal(audio.sources.size, 0); assert.equal(audio.nextTime, 0);
});

test('interruption discards a speech file that is still decoding', async () => {
  const audio = new LiveAudio(() => {}, () => {});
  let finish;
  audio.context = { decodeAudioData: () => new Promise(resolve => { finish = resolve; }) };
  let queued = false;
  audio.enqueueBuffer = () => { queued = true; };
  const pending = audio.playFile('AAAA');
  await new Promise(resolve => setImmediate(resolve));
  audio.interrupt();
  finish({});
  await pending;
  assert.equal(queued, false);
});
