import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function POST(request: Request) {
	try {
		const body = await request.json().catch(() => ({}));
		const res = await fetch(`${BACKEND_URL}/api/start_stream`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
		const data = await res.json().catch(() => ({}));
		return NextResponse.json(data, { status: res.status });
	} catch (err) {
		return NextResponse.json({ error: "backend_unreachable" }, { status: 502 });
	}
}


