const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:5000";

export async function GET(
	request: Request,
	context: any
) {
	try {
		const stream_id = context?.params?.stream_id;
		if (typeof stream_id !== "string" || !stream_id) {
			return new Response(JSON.stringify({ error: "missing_stream_id" }), { status: 400 });
		}
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


