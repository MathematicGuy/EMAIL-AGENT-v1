import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE_URL } from '../../lib/apiConfig';
import {
  createConversation,
  listConversationMessages,
  listConversations,
  LOCAL_ASSISTANT_SCOPE,
  sendConversationMessage,
  streamTurnEvents,
} from './assistantApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Assistant Runtime local API client', () => {
  it('creates a conversation on the local FastAPI runtime', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ conversation_id: 'conv-local' }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      createConversation(LOCAL_ASSISTANT_SCOPE)
    ).resolves.toBe('conv-local');

    expect(fetchMock).toHaveBeenCalledWith(
      `${API_BASE_URL}/v1/conversations`,
      expect.objectContaining({ method: 'POST' })
    );
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      actor_id: 'demo-user',
      project_id: 'demo-project',
      workspace_id: 'demo-workspace',
    });
  });

  it('parses streamed Assistant Runtime events', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode('id: 1\nevent: assistant.delta\ndata: {"delta":"Xin')
        );
        controller.enqueue(
          encoder.encode(
            ' chào"}\n\nid: 2\nevent: assistant.completed\ndata: {"message_id":"msg-1"}\n\n'
          )
        );
        controller.close();
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      )
    );
    const events: Array<{ event: string; data: Record<string, unknown> }> = [];

    await streamTurnEvents({
      conversationId: 'conv-local',
      turnId: 'turn-local',
      scope: LOCAL_ASSISTANT_SCOPE,
      onEvent: ({ event, data }) => events.push({ event, data }),
    });

    expect(events).toEqual([
      { event: 'assistant.delta', data: { delta: 'Xin chào' } },
      { event: 'assistant.completed', data: { message_id: 'msg-1' } },
    ]);
  });

  it('sends immutable attachment refs with the chat message', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          conversation_id: 'conv-local',
          turn_id: 'turn-local',
          events_url: '/events',
        }),
        {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        }
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    await sendConversationMessage({
      conversationId: 'conv-local',
      text: 'Tóm tắt file này',
      modelId: 'deepseek-openrouter',
      attachmentRefs: [
        {
          ref_id: 'source-chat-1',
          checksum: 'sha256:chat-1',
          source_id: 'sales.csv',
          media_type: 'text/csv',
        },
      ],
      scope: LOCAL_ASSISTANT_SCOPE,
    });

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(request.body));
    expect(body.model_id).toBe('deepseek-openrouter');
    expect(body.content.attachment_refs).toEqual([
      expect.objectContaining({
        ref_id: 'source-chat-1',
        checksum: 'sha256:chat-1',
      }),
    ]);
  });

  it('serializes the clarification reply and the resume cursor', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) =>
      Promise.resolve(
        String(input).includes('/events?')
          ? new Response(JSON.stringify({ items: [] }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            })
          : new Response(
              JSON.stringify({
                conversation_id: 'conv-local',
                turn_id: 'turn-1',
                events_url: '/events',
              }),
              { status: 202, headers: { 'Content-Type': 'application/json' } }
            )
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    await sendConversationMessage({
      conversationId: 'conv-local',
      text: 'Quý 2',
      modelId: 'gemini-3.6-flash',
      replyToTurnId: 'turn-1',
      scope: LOCAL_ASSISTANT_SCOPE,
    });
    expect(
      JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))
    ).toMatchObject({ reply_to_turn_id: 'turn-1' });

    await streamTurnEvents({
      conversationId: 'conv-local',
      turnId: 'turn-1',
      afterSequence: 7,
      scope: LOCAL_ASSISTANT_SCOPE,
      onEvent: vi.fn(),
    });
    expect(String(fetchMock.mock.calls[1][0])).toContain('after_sequence=7');
    expect(
      (fetchMock.mock.calls[1][1] as RequestInit).headers
    ).toMatchObject({ 'Last-Event-ID': '7' });
  });

  it('omits the clarification reply when the message starts a new turn', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          conversation_id: 'conv-local',
          turn_id: 'turn-1',
          events_url: '/events',
        }),
        { status: 202, headers: { 'Content-Type': 'application/json' } }
      )
    );
    vi.stubGlobal('fetch', fetchMock);

    await sendConversationMessage({
      conversationId: 'conv-local',
      text: 'Tạo báo cáo',
      modelId: 'gemini-3.5-flash-lite',
      scope: LOCAL_ASSISTANT_SCOPE,
    });

    expect(
      JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))
    ).not.toHaveProperty('reply_to_turn_id');
  });

  it('loads persisted conversations and their messages', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/messages?')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  message_id: 'message-1',
                  role: 'user',
                  content: { type: 'text', text: 'Tin nhắn đã lưu', metadata: {} },
                  created_at: '2026-07-27T09:00:00Z',
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          )
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            items: [
              {
                conversation_id: 'conv-saved',
                title: 'Cuộc trò chuyện đã lưu',
                message_count: 1,
                last_activity_at: '2026-07-27T09:00:00Z',
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(listConversations(LOCAL_ASSISTANT_SCOPE)).resolves.toEqual([
      expect.objectContaining({ conversation_id: 'conv-saved' }),
    ]);
    await expect(
      listConversationMessages('conv-saved', LOCAL_ASSISTANT_SCOPE)
    ).resolves.toEqual([
      expect.objectContaining({
        message_id: 'message-1',
        content: expect.objectContaining({ text: 'Tin nhắn đã lưu' }),
      }),
    ]);

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/v1/conversations?actor_id=demo-user'
    );
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      '/v1/conversations/conv-saved/messages?'
    );
  });
});
