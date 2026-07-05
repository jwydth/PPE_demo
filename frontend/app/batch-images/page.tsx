"use client";

import { BatchImagesPanel } from "@/components/BatchImagesPanel";

export default function BatchImagesPage() {
  return (
    <main className="min-h-screen bg-[#0a0c0f] text-zinc-100 p-6 md:p-10">
      <header className="mb-8 border-b border-zinc-800 pb-6">
        <div className="flex items-center gap-3 mb-2">
          <span className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
          <p className="font-mono text-orange-500 text-xs tracking-widest uppercase">
            De Heus / Smart Factory / Batch Image Review
          </p>
        </div>
        <h1 className="font-sans text-2xl font-bold text-zinc-100">
          PPE Batch Image Analysis
        </h1>
        <p className="text-zinc-500 text-sm mt-1 font-mono">
          Upload multiple image frames and review PPE compliance results for each image
        </p>
      </header>

      <BatchImagesPanel />
    </main>
  );
}
