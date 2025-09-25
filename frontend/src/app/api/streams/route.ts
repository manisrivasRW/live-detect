import { NextResponse } from "next/server";

type StreamRecord = {
	 id: string;
	 name: string;
	 url: string;
	 status: "pending" | "connected" | "error";
};

// In-memory placeholder store (for local dev only)
const streams: StreamRecord[] = [];

export async function GET() {
	return NextResponse.json({ streams });
}

export async function POST(request: Request) {
	try {
		const body = await request.json();
		const name: string = (body?.name ?? "").toString().trim();
		const url: string = (body?.url ?? "").toString().trim();

		if (!name || !url) {
			return NextResponse.json(
				{ error: "Both 'name' and 'url' (RTSP) are required." },
				{ status: 400 }
			);
		}

		// Very light RTSP-ish validation (placeholder)
		if (!/^rtsp(s)?:\/\//i.test(url)) {
			return NextResponse.json(
				{ error: "URL must be an RTSP link (e.g., rtsp://...)." },
				{ status: 400 }
			);
		}

		const record: StreamRecord = {
			id: crypto.randomUUID(),
			name,
			url,
			status: "pending",
		};
		streams.push(record);

		// NOTE: In production, call your backend here to register the stream
		// and return whatever metadata you need to render the player.

		return NextResponse.json({ stream: record }, { status: 201 });
	} catch (err) {
		return NextResponse.json(
			{ error: "Invalid request body." },
			{ status: 400 }
		);
	}
}

export async function DELETE(request: Request) {
	const { searchParams } = new URL(request.url);
	const id = searchParams.get("id");
	if (!id) return NextResponse.json({ error: "id is required" }, { status: 400 });
	const idx = streams.findIndex((s) => s.id === id);
	if (idx === -1) return NextResponse.json({ error: "not found" }, { status: 404 });
	const [removed] = streams.splice(idx, 1);
	return NextResponse.json({ removed });
}


export async function PATCH(request: Request) {
	try {
		const body = await request.json();
		const id: string = (body?.id ?? "").toString();
		const name: string = (body?.name ?? "").toString().trim();
		if (!id || !name) {
			return NextResponse.json(
				{ error: "Both 'id' and 'name' are required." },
				{ status: 400 }
			);
		}
		const record = streams.find((s) => s.id === id);
		if (!record) {
			return NextResponse.json({ error: "not found" }, { status: 404 });
		}
		record.name = name;
		return NextResponse.json({ stream: record });
	} catch (err) {
		return NextResponse.json({ error: "Invalid request body." }, { status: 400 });
	}
}


