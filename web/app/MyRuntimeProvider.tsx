"use client";

import {
  AssistantRuntimeProvider,
  type AssistantTransportConnectionMetadata,
  type ThreadMessageLike,
  unstable_createMessageConverter as createMessageConverter,
  useAssistantTransportRuntime,
  useExternalMessageConverter,
} from "@assistant-ui/react";
import type { ReactNode } from "react";

import { publishSessionLimitReached } from "@/lib/session-limit";

type JSONValue =
  | string
  | number
  | boolean
  | null
  | JSONValue[]
  | { [key: string]: JSONValue };
type JSONObject = { [key: string]: JSONValue };

// The backend (POST /assistant) streams assistant-ui-native messages: a role
// plus an ordered list of parts. Text parts stream token-by-token; tool-call
// parts carry the tool name, args, and (once done) the result. This is the
// shape the backend in api/transport.py emits.
type NativePart =
  | { type: "text"; text: string }
  | {
      type: "tool-call";
      toolCallId: string;
      toolName: string;
      argsText?: string;
      args?: JSONObject;
      result?: JSONValue;
      done?: boolean;
    };

type NativeMessage = {
  role: "user" | "assistant" | "system";
  parts: NativePart[];
};

type State = {
  messages: NativeMessage[];
  // Set by the backend when the per-session token cap is hit (ADR-0011). The
  // converter mirrors it into the session-limit store, which the
  // SessionLimitBanner reads to render the "Start new conversation" CTA. The
  // frontend never writes this field: server-trusted enforcement is the point.
  sessionLimitReached?: boolean;
};

const convertMessage: useExternalMessageConverter.Callback<NativeMessage> = (
  message,
): ThreadMessageLike => ({
  role: message.role,
  content: message.parts.map((part) => {
    if (part.type === "tool-call") {
      return {
        type: "tool-call",
        toolCallId: part.toolCallId,
        toolName: part.toolName,
        argsText: part.argsText ?? "",
        args: part.args ?? {},
        ...(part.result !== undefined ? { result: part.result } : {}),
      };
    }
    return { type: "text", text: part.text };
  }),
});

const MessageConverter = createMessageConverter(convertMessage);

const converter = (
  state: State,
  connectionMetadata: AssistantTransportConnectionMetadata,
) => {
  // Mirror the server-trusted session-limit flag into the store the banner
  // reads (see web/lib/session-limit.ts for why this path, not the transport
  // extras hook).
  publishSessionLimitReached(state.sessionLimitReached === true);

  // Commands still in flight are not in state yet; surface them optimistically.
  // An add-message command's `message` is already in native shape.
  const optimistic = connectionMetadata.pendingCommands
    .filter((c) => c.type === "add-message")
    .map((c) => c.message as NativeMessage);

  return {
    messages: MessageConverter.toThreadMessages([
      ...state.messages,
      ...optimistic,
    ]),
    isRunning: connectionMetadata.isSending || false,
  };
};

export function MyRuntimeProvider({ children }: { children: ReactNode }) {
  const runtime = useAssistantTransportRuntime({
    initialState: {
      messages: [],
    },
    // Same-origin path; next.config.js rewrites /assistant to the backend.
    api: "/assistant",
    headers: {},
    converter,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
