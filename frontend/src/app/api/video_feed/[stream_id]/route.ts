const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function GET(
	request: Request,
	{ params }: { params: { stream_id: string } }
) {
	try {
		const { stream_id } = params;
		const res = await fetch(`${BACKEND_URL}/video_feed/${encodeURIComponent(stream_id)}`);
		if (!res.ok || !res.body) {
			return new Response(JSON.stringify({ error: "backend_error" }), { status: res.status || 502 });
		}
		return new Response(res.body, {
			status: 200,
			headers: {
				"Content-Type": "multipart/x-mixed-replace; boundary=frame",
				"Cache-Control": "no-store",
			},
		});
	} catch (err) {
		return new Response(JSON.stringify({ error: "backend_unreachable" }), { status: 502 });
	}
}


