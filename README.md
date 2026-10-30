# WORK PANIC v0.2 — AI-connected MVP

This version upgrades the first prototype to a real backend architecture.

## Run
1. Install Node.js 20+
2. Run `npm install`
3. Copy `.env.example` to `.env`
4. Put your API key in `OPENAI_API_KEY`
5. Run `npm start`
6. Open http://localhost:3000

The browser never receives the API key. The server sends text and optional screenshot data to the OpenAI Responses API.

## What it does
- text message analysis
- screenshot upload
- tasks
- deadline
- priority
- 3 reply tones
- structured JSON response

## Before public launch
- authentication
- rate limits
- Stripe
- secure storage policy
- privacy/terms
- logging and analytics
- delete uploaded images after processing
- production hosting/domain
