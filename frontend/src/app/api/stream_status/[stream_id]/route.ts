import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function GET(
	request: Request,
	{ params }: { params: { stream_id: string } }
) {
	try {
		const { stream_id } = params;
		const res = await fetch(`${BACKEND_URL}/api/stream_status/${encodeURIComponent(stream_id)}`);
		const data = await res.json().catch(() => ({}));
		return NextResponse.json(data, { status: res.status });
	} catch (err) {
		return NextResponse.json({ error: "backend_unreachable" }, { status: 502 });
	}
}


