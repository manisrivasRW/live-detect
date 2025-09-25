import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function GET() {
	try {
		const res = await fetch(`${BACKEND_URL}/api/get-suspicious-data`, { cache: "no-store" });
		const data = await res.json().catch(() => ({}));
		return NextResponse.json(data, { status: res.status });
	} catch (err) {
		return NextResponse.json({ status: "error", error: "backend_unreachable" }, { status: 502 });
	}
}


