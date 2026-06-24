// app/api/chat/route.ts
// BFF proxy: forwards the user's JWT (if present) + multipart form data to FastAPI.
import { NextRequest, NextResponse } from 'next/server';
import { backendBaseUrl } from '../../../lib/backend';

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();

    const headers: Record<string, string> = {};
    const authHeader = req.headers.get('authorization');
    if (authHeader) headers['Authorization'] = authHeader;

    const response = await fetch(`${backendBaseUrl()}/chat`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      return NextResponse.json(
        { response: `Backend Error ${response.status}: ${errorText}` },
        { status: response.status },
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : 'unknown error';
    return NextResponse.json(
      { response: `Connection Error: Could not reach backend. Details: ${message}` },
      { status: 502 },
    );
  }
}
