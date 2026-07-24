// Example usage: app/flow-builder/page.jsx
// React Flow reads window/DOM measurements, so load it client-side only.
"use client";

import dynamic from "next/dynamic";

const FlowBuilder = dynamic(() => import("../components/FlowBuilder"), {
  ssr: false,
});

export default function FlowBuilderPage() {
  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <FlowBuilder />
    </div>
  );
}
