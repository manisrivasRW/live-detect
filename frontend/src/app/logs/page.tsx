export default function LogsPage() {
	// Hardcoded sample logs for now
	const logs = [
		{
			img_url: "/vercel.svg",
			name: "John Doe",
			stream_name: "Entrance Cam",
			timestamp: "2025-09-25 14:22:10",
			police_station: "Central Station",
		},
		{
			img_url: "/next.svg",
			name: "Jane Roe",
			stream_name: "Lobby Cam",
			timestamp: "2025-09-25 14:18:47",
			police_station: "North Precinct",
		},
	];

	return (
		<div className="min-h-dvh bg-black text-white px-4 sm:px-6 lg:px-8 py-6">
			<h1 className="text-2xl sm:text-3xl font-semibold">Suspects Logs</h1>
			<div className="mt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
				{logs.map((log, idx) => (
					<LogCard key={idx} {...log} />
				))}
			</div>
		</div>
	);
}

function LogCard({ img_url, name, stream_name, timestamp, police_station }: { img_url: string; name: string; stream_name: string; timestamp: string; police_station: string }) {
	return (
		<div className="rounded-2xl bg-[#1f1f1f] p-4 border border-white/10">
			<div className="w-full flex justify-start">
				<img src={img_url} alt={name} className="h-24 w-24 rounded object-cover bg-white" />
			</div>
			<div className="mt-3 space-y-1.5">
				<div className="text-lg font-semibold break-words">{name}</div>
				<div className="text-sm text-white/80 break-words">{stream_name}</div>
				<div className="text-xs text-white/60">{timestamp}</div>
				<div className="text-xs text-white/70">Police Station: {police_station}</div>
			</div>
		</div>
	);
}


