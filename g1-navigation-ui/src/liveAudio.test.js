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

test('playback acknowledgement waits for scheduled playback and interrupt suppresses it', async () => {
  const acknowledgements = [];
  const audio = new LiveAudio(() => {}, () => {}, (type, id) => acknowledgements.push([type, id]));
  audio.context = { currentTime: 0, state: 'running', destination: {}, createBufferSource: () => ({ connect(){}, disconnect(){}, start(){}, stop(){} }) };
  audio.enqueueBuffer({ duration: .1 }, 'a');
  assert.deepEqual(acknowledgements, []);
  audio.context.currentTime = .03;
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.deepEqual(acknowledgements, [['playback_started', 'a']]);
  audio.enqueueBuffer({ duration: .1 }, 'b');
  audio.interrupt();
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.deepEqual(acknowledgements, [['playback_started', 'a']]);
});

test('a mobile clock stuck at zero still acknowledges completed playback once', async () => {
  const acknowledgements = [];
  const audio = new LiveAudio(() => {}, () => {}, (type,id) => acknowledgements.push([type,id]));
  const sources=[];
  audio.context={currentTime:0,state:'running',destination:{},getOutputTimestamp:()=>({contextTime:0}),createBufferSource:()=>{
    const source={connect(){},disconnect(){},start(){},stop(){}};sources.push(source);return source;
  }};
  audio.enqueueBuffer({duration:.1},'mobile');
  assert.deepEqual(acknowledgements,[]);
  sources[0].onended();
  assert.deepEqual(acknowledgements,[['playback_started','mobile'],['playback_ended','mobile']]);
  await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(acknowledgements.length,2);
  audio.interrupt();
});


test('full replies suppress microphone packets without stopping playback or changing manual mute', () => {
  const packets=[];
  const audio=new LiveAudio(packet=>packets.push(packet),()=>{});
  audio.finishReplies=true;
  audio.setListening(false);
  audio.sendMicrophone('echo');
  audio.sources.add({});
  audio.setListening(true);
  audio.sendMicrophone('passenger interruption');
  audio.sources.clear();
  audio.setListening(false); // Acoustic tail remains gated by the server.
  audio.sendMicrophone('tail');
  assert.deepEqual(packets,[]);
  audio.mute(true);
  audio.setListening(true);
  audio.sendMicrophone('muted');
  audio.mute(false);
  audio.sendMicrophone('next question');
  assert.deepEqual(packets,['next question']);
  audio.closed=true;
  audio.sendMicrophone('closed');
  assert.equal(packets.length,1);
});
