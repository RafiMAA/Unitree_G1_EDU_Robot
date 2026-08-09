# Unitree G1 Multilingual RAG Conversation System

## Overview

This document explains the Retrieval-Augmented Generation (RAG)
conversation system used in the Unitree G1 robot.

The goal is simple:

> A passenger speaks to the robot, the system understands the question,
> retrieves trusted information when needed, generates an answer in the
> correct language, and speaks the answer through the robot.

## High-Level Architecture

``` text
                   UNITREE G1
                       │
                  Microphone
                       │
                       ▼
                WebRTC VAD
              "Is this speech?"
                       │
                       ▼
                   Whisper
                Speech → Text
                       │
                       ▼
               Session Manager
              Language + History
                       │
                       ▼
                LangChain Agent
                /             \
               /               \
              ▼                 ▼
        RAG Retrieval         Gemini LLM
              │                   ▲
              ▼                   │
      Gemini Embeddings           │
              │                   │
              ▼                   │
            FAISS                 │
              │                   │
              └── relevant facts ─┘
                                  │
                                  ▼
                             Text Answer
                                  │
                                  ▼
                              Edge TTS
                                  │
                                  ▼
                             G1 Speaker
```

## End-to-End Flow

1.  The passenger speaks into the Unitree G1 microphone.
2.  WebRTC VAD detects whether the incoming audio contains human speech.
3.  Whisper converts the captured speech into text.
4.  The session manager keeps track of the passenger's selected language
    and recent conversation history.
5.  The LangChain agent receives the question and decides whether
    knowledge-base retrieval is needed.
6.  For RAG retrieval, the question is converted into an embedding.
7.  FAISS compares the question embedding with embeddings created from
    the knowledge-base chunks.
8.  The most relevant chunks are returned as trusted context.
9.  Gemini receives the question, conversation context, instructions,
    and retrieved facts and generates a concise answer.
10. Edge TTS converts the generated text into speech in the selected
    language.
11. The answer is played through the Unitree G1 speaker.

------------------------------------------------------------------------

## 1. Unitree G1 and Microphone

The Unitree G1 is the physical robot hosting or interacting with the
conversation system.

The microphone captures the passenger's voice and provides the raw audio
used by the speech pipeline.

``` text
Passenger
    ↓
Microphone
    ↓
Audio stream
```

The microphone itself does not understand speech. The following
components determine when speech occurs and what was said.

------------------------------------------------------------------------

## 2. WebRTC VAD

**VAD** means **Voice Activity Detection**.

Its purpose is to determine whether incoming audio contains human speech
rather than silence or background noise.

``` text
Microphone
    ↓
WebRTC VAD
    ↓
Noise/Silence → Ignore
Speech        → Record
```

This is especially useful in an airport, where the microphone may
capture announcements, luggage noise, conversations, air conditioning,
and other background sounds.

VAD acts as the **speech gatekeeper** before speech recognition.

------------------------------------------------------------------------

## 3. Whisper

Whisper is the **Speech-to-Text (STT)** component.

It converts recorded passenger speech into written text.

``` text
Passenger audio
      ↓
   Whisper
      ↓
Passenger text
```

Example:

``` text
Audio: "How can I install PickMe?"

            ↓ Whisper

Text:  "How can I install PickMe?"
```

Whisper also supports multilingual speech recognition and language
detection.

In this architecture, Whisper runs locally, so speech recognition can be
performed on the robot/computer without sending microphone audio to the
LLM.

------------------------------------------------------------------------

## 4. Session Manager

The session manager stores information about the current passenger
conversation.

Typical session information includes:

-   selected language;
-   recent chat history;
-   turn count;
-   current conversation state.

Example:

``` text
PassengerSession
├── language = "si"
├── turn_count = 4
└── chat_history = [...]
```

This allows follow-up questions to make sense.

For example:

``` text
Passenger: How can I install PickMe?
Robot: ...

Passenger: Is it available on iPhone?
```

The chat history helps the system understand that **"it"** refers to the
PickMe application.

A new passenger should receive a new session so that previous passenger
conversations are not mixed with the new conversation.

------------------------------------------------------------------------

## 5. LangChain Agent

LangChain is the application framework that coordinates the LLM,
conversation context, prompts, and retrieval tools.

The LangChain agent acts as the **AI workflow controller**.

``` text
                  LangChain Agent
                  /      |       \
                 /       |        \
                ▼        ▼         ▼
             Gemini   RAG Tool   Chat History
```

For a general conversational question, the agent can send the
appropriate context to Gemini.

For a question requiring trusted PickMe information, the agent can use
the RAG retrieval tool before generating the final answer.

Example:

``` text
Passenger:
"How do I install PickMe?"

        ↓

LangChain Agent

        ↓

search_knowledge_base()

        ↓

Relevant PickMe information

        ↓

Gemini
```

LangChain is not the LLM itself. It organizes how the LLM and other
tools work together.

------------------------------------------------------------------------

# RAG System

## 6. What is RAG?

**RAG** means **Retrieval-Augmented Generation**.

Instead of asking the LLM to answer only from its general model
knowledge, the system first searches a maintained project knowledge base
for relevant information.

### Without RAG

``` text
Question
   ↓
Gemini
   ↓
Answer based mainly on model knowledge
```

### With RAG

``` text
Question
   ↓
Retrieve relevant project information
   ↓
Question + retrieved facts
   ↓
Gemini
   ↓
Grounded answer
```

For this project, RAG helps the robot answer using maintained PickMe
information.

------------------------------------------------------------------------

## 7. Knowledge Base

The knowledge base contains Markdown (`.md`) documents with information
the robot is expected to know.

Examples include information about:

``` text
PickMe services
App installation
Sri Lankan locations
Robot capabilities
```

Markdown is useful because it is easy for humans to edit and easy for
the application to process.

The Markdown documents are the **source knowledge**. FAISS does not
replace these documents; it provides a searchable representation of
their contents.

------------------------------------------------------------------------

## 8. Chunking

Large documents are divided into smaller pieces called **chunks** before
embedding.

``` text
Document
   ↓
Chunking
   ↓
Chunk 1
Chunk 2
Chunk 3
...
```

In this architecture, documents are split into approximately:

``` text
500 characters per chunk
50-character overlap
```

The overlap helps preserve context when information crosses a chunk
boundary.

Smaller chunks allow the retrieval system to return the specific pieces
of information relevant to a passenger's question rather than sending
entire documents to the LLM.

------------------------------------------------------------------------

## 9. Gemini Embeddings

An embedding model converts text into a numerical vector representing
semantic meaning.

Conceptually:

``` text
"How do I install PickMe?"

            ↓

     Gemini Embeddings

            ↓

[0.13, -0.82, 0.45, 0.11, ...]
```

The same process is applied to knowledge-base chunks.

``` text
Chunk 1 → Vector A
Chunk 2 → Vector B
Chunk 3 → Vector C
...
```

Texts with similar meanings should have vectors that are relatively
close in the embedding space.

For example:

``` text
"How do I download PickMe?"

and

"Where can I install the PickMe app?"
```

are semantically similar even though they do not use exactly the same
words.

Embeddings make this kind of semantic retrieval possible.

------------------------------------------------------------------------

## 10. FAISS

FAISS is the local vector similarity search component.

After knowledge-base chunks have been converted into embeddings, FAISS
indexes those vectors.

``` text
Knowledge Documents
       ↓
    Chunking
       ↓
Gemini Embeddings
       ↓
     Vectors
       ↓
      FAISS
```

When a passenger asks a question, the question is also converted into an
embedding.

``` text
Passenger Question
       ↓
Question Embedding
       ↓
      FAISS
       ↓
Find similar knowledge vectors
       ↓
Relevant chunks
```

FAISS does **not** generate the answer. Its job is to find the stored
chunks whose meanings are most similar to the question.

In this system, the most relevant chunks are passed back to the agent as
context.

------------------------------------------------------------------------

## 11. RAG Retrieval Example

Suppose the passenger asks:

> "How can I install PickMe?"

The retrieval process is:

``` text
"How can I install PickMe?"
              │
              ▼
       Gemini Embeddings
              │
              ▼
        Question Vector
              │
              ▼
             FAISS
              │
              ▼
Compare with knowledge-base vectors
              │
              ▼
      Most relevant chunks
              │
              ▼
        LangChain Agent
              │
              ▼
             Gemini
```

The retrieved information might contain instructions from the project's
PickMe installation document.

Gemini can then create a natural-language answer using those facts.

------------------------------------------------------------------------

## 12. Gemini LLM

Gemini is the **Large Language Model (LLM)** responsible for generating
the final conversational answer.

It can receive information such as:

``` text
System instructions
        +
Selected language
        +
Recent chat history
        +
Passenger question
        +
Retrieved knowledge
```

Conceptually:

``` text
SYSTEM:
You are an airport customer assistant.

LANGUAGE:
Sinhala

CHAT HISTORY:
...

RETRIEVED KNOWLEDGE:
...

PASSENGER:
How do I install PickMe?
```

Gemini uses this context to produce a concise response.

The important difference between the AI components is:

``` text
Whisper
Speech → Text

Embedding Model
Text → Vectors

FAISS
Question Vector → Relevant Knowledge

Gemini LLM
Question + Context → Natural-Language Answer
```

------------------------------------------------------------------------

## 13. Edge TTS

**TTS** means **Text-to-Speech**.

Edge TTS converts Gemini's text response into audio.

``` text
Gemini response
      ↓
   Edge TTS
      ↓
Speech audio
      ↓
G1 speaker
```

It performs the opposite role of Whisper:

``` text
Whisper:  Speech → Text

Edge TTS: Text → Speech
```

A voice can be selected according to the passenger's session language.

------------------------------------------------------------------------

# Knowledge Preparation vs Live Conversation

The system contains two related processes.

## Knowledge Preparation

This happens when the knowledge base needs to be indexed or rebuilt.

``` text
Markdown Documents
        ↓
     Chunking
        ↓
 Gemini Embeddings
        ↓
     FAISS Index
        ↓
   Save to Cache
```

## Live Passenger Query

This happens during a conversation.

``` text
Passenger Question
        ↓
     Whisper
        ↓
       Text
        ↓
 LangChain Agent
        ↓
Question Embedding
        ↓
      FAISS
        ↓
Relevant Knowledge
        ↓
      Gemini
        ↓
   Text Answer
        ↓
    Edge TTS
        ↓
    G1 Speaker
```

The documents normally do **not** need to be embedded again for every
passenger question. Their vectors can be stored in the FAISS index.

------------------------------------------------------------------------

# Cache

Creating embeddings for every knowledge chunk every time the robot
starts would be unnecessary.

The FAISS index can therefore be cached.

``` text
Robot starts
     ↓
Has knowledge base changed?
     │
 ┌───┴────┐
 │        │
 NO      YES
 │        │
 ▼        ▼
Load     Recreate embeddings
cache         ↓
          Rebuild FAISS
              ↓
          Save cache
```

This reduces repeated embedding work and improves startup efficiency.

------------------------------------------------------------------------

# Role of ROS 2

ROS 2 provides the robotics communication and orchestration layer around
the conversation pipeline.

The conversation node can publish information such as:

``` text
g1/speech_text
g1/agent_response
g1/conversation_state
```

Other robot components can subscribe to these topics.

For example:

``` text
                  g1/agent_response
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Speaker     Display      Logger
```

This keeps the AI implementation loosely coupled from other robot
components.

------------------------------------------------------------------------

# Complete System Summary

``` text
Passenger
   │
   ▼
Unitree G1 Microphone
   │
   ▼
WebRTC VAD
Detect speech
   │
   ▼
Whisper
Speech → Text
   │
   ▼
Session Manager
Language + Conversation History
   │
   ▼
LangChain Agent
   │
   ├─────────────── RAG needed? ───────────────┐
   │                                            │
   │                                            ▼
   │                                   Embed Question
   │                                            │
   │                                            ▼
   │                                           FAISS
   │                                            │
   │                                            ▼
   │                                    Relevant Chunks
   │                                            │
   ◄────────────────────────────────────────────┘
   │
   ▼
Gemini LLM
Question + History + Retrieved Facts
   │
   ▼
Text Answer
   │
   ▼
Edge TTS
Text → Speech
   │
   ▼
Unitree G1 Speaker
   │
   ▼
Passenger hears response
```

# Component Cheat Sheet

  Component           Main Purpose
  ------------------- ------------------------------------------------------------
  Unitree G1          Physical robot platform
  Microphone          Captures passenger audio
  WebRTC VAD          Detects speech and filters silence/noise
  Whisper             Converts speech to text
  Session Manager     Stores language and recent conversation context
  LangChain           Coordinates LLM, tools, prompts, history, and retrieval
  RAG                 Retrieves project knowledge before answer generation
  Markdown files      Human-maintained source knowledge
  Chunking            Splits documents into searchable pieces
  Gemini Embeddings   Converts text meaning into vectors
  FAISS               Finds semantically similar knowledge chunks
  Gemini LLM          Generates the final natural-language response
  Edge TTS            Converts the response text into speech
  ROS 2               Connects and coordinates conversation and robot components

## One-Sentence Explanation

> **The Unitree G1 listens to a passenger, converts speech to text, uses
> LangChain and RAG to retrieve relevant trusted information from a
> FAISS knowledge index, asks Gemini to generate an answer using that
> context, and converts the answer back to speech for the passenger.**

## Key Idea

The most important concept is that **LangChain, RAG, FAISS, embeddings,
and Gemini perform different jobs**:

``` text
LangChain  → controls the AI workflow
RAG        → retrieval + generation approach
Embeddings → represent text meaning numerically
FAISS      → searches those vectors
Gemini     → generates the final answer
```

Together, they allow the robot to give conversational answers grounded
in the project's maintained knowledge instead of depending only on the
LLM's general knowledge.
