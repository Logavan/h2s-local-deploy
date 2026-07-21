"use client";

import { useEffect, useState, useRef } from "react";
import { easeQuadInOut } from "d3-ease"; // Import specific ease functions
import { graphviz } from "d3-graphviz"; // Import graphviz directly
import type { Graphviz } from "d3-graphviz"; // Import only the type for compile-time checks
import { easeLinear } from "d3-ease"; // Import specific ease functions
import "d3-transition"; // Import d3-transition for its side effects to extend d3-selection

interface GraphvizViewerProps {
  dotString: string;
  onSvgRendered?: (svgString: string) => void;
}

// Predefined color palettes for nodes
const nodeColorPalettes = [
  { fill: "#e0f7fa", border: "#00bcd4" }, // Cyan light
  { fill: "#e8f5e9", border: "#4caf50" }, // Green light
  { fill: "#fff3e0", border: "#ff9800" }, // Orange light
  { fill: "#fbe9e7", border: "#ff5722" }, // Deep Orange light
  { fill: "#ede7f6", border: "#673ab7" }, // Deep Purple light
  { fill: "#e3f2fd", border: "#2196f3" }, // Blue light
  { fill: "#fce4ec", border: "#e91e63" }, // Pink light
  { fill: "#f3e5f5", border: "#9c27b0" }, // Purple light
  { fill: "#e1f5fe", border: "#03a9f4" }, // Light Blue light
  { fill: "#e0f2f1", border: "#009688" }, // Teal light
  { fill: "#f1f8e9", border: "#8bc34a" }, // Light Green light
  { fill: "#ffe0b2", border: "#ffc107" }, // Amber light
  { fill: "#f8bbd0", border: "#e91e63" }, // Pink light (duplicate for more options)
  { fill: "#d1c4e9", border: "#673ab7" }, // Deep Purple light (duplicate)
  { fill: "#bbdefb", border: "#2196f3" }, // Blue light (duplicate)
  { fill: "#ffccbc", border: "#ff5722" }, // Deep Orange light (duplicate)
  { fill: "#c8e6c9", border: "#4caf50" }, // Green light (duplicate)
  { fill: "#b2ebf2", border: "#00bcd4" }, // Cyan light (duplicate)
  { fill: "#f0f4c3", border: "#cddc39" }, // Lime light
  { fill: "#e0e0e0", border: "#9e9e9e" }, // Grey light
  { fill: "#cfd8dc", border: "#607d8b" }, // Blue Grey light
];

// A separate palette for edge colors to ensure good contrast
const edgeColorPalettes = [
  "#e91e63", // Pink
  "#4caf50", // Green
  "#ff9800", // Orange
  "#2196f3", // Blue
  "#9c27b0", // Purple
  "#00bcd4", // Cyan
  "#ff5722", // Deep Orange
  "#673ab7", // Deep Purple
  "#03a9f4", // Light Blue
  "#009688", // Teal
  "#8bc34a", // Light Green
  "#ffc107", // Amber
  "#cddc39", // Lime
  "#9e9e9e", // Grey
  "#607d8b", // Blue Grey
];

// -------------------- STEP GENERATOR --------------------

function generateDotSteps(rawDotString: string): { steps: string[]; nodesWithColors: { [key: string]: { fill: string; border: string; edgeColor: string; originalAttributes: string } } } {
  const allLines = rawDotString.split("\n");

  const nodesWithColors: { [key: string]: { fill: string; border: string; edgeColor: string; originalAttributes: string } } = {};
  let colorIndex = 0;

  // Helper to assign a color to a node if it doesn't have one, or use existing
  const assignColorToNode = (nodeName: string) => {
    if (!nodesWithColors[nodeName]) {
      let existingFillColor = null;
      let existingBorderColor = null;
      let originalAttributes = '';

      // Try to find existing attributes in the rawDotString for this node
      const nodeDefinitionMatch = rawDotString.match(new RegExp(`\\b${nodeName}\\b\\s*\\[([^\\]]*)\\]`));
      if (nodeDefinitionMatch) {
        originalAttributes = nodeDefinitionMatch[1];
        const fillColorMatch = originalAttributes.match(/fillcolor="([^"]*)"/);
        if (fillColorMatch) existingFillColor = fillColorMatch[1];
        const borderColorMatch = originalAttributes.match(/color="([^"]*)"/);
        if (borderColorMatch) existingBorderColor = borderColorMatch[1];
      }

      // Always assign a color from the palette, overriding any existing ones
      nodesWithColors[nodeName] = {
        ...nodeColorPalettes[colorIndex % nodeColorPalettes.length],
        edgeColor: edgeColorPalettes[colorIndex % edgeColorPalettes.length], // Assign an edge color
        originalAttributes,
      };
      colorIndex++;
    }
  };

  // First pass: Extract ALL node names from the raw DOT string
  const allNodeMentionsRegex = /(\b[a-zA-Z_][a-zA-Z0-9_]*\b)(?:\s*\[[^\]]*\]|\s*->|\s*;|$)/g;
  let match;
  while ((match = allNodeMentionsRegex.exec(rawDotString)) !== null) {
    const nodeName = match[1];
    if (!['digraph', 'graph', 'subgraph', 'node', 'edge', 'rankdir', 'label', 'style', 'shape', 'fillcolor', 'color', 'fontname', 'fontsize', 'penwidth'].includes(nodeName.toLowerCase())) {
      assignColorToNode(nodeName);
    }
  }

  const globalAttributes = `
  rankdir=LR;
  node [shape=box, fontname="Arial", fontsize=12, penwidth=1.5, margin="0.2,0.1"];
  edge [penwidth=1.5];
  `;

  const steps: string[] = [];
  const currentElements: string[] = []; // Stores nodes and edges as they are added

  // Initial step: only global attributes
  steps.push(`digraph G {${globalAttributes}}`);

  // Filter out global attributes and comments for incremental processing
  const relevantLines = allLines.filter(line =>
    line.trim() &&
    !line.trim().startsWith("//") &&
    !line.trim().startsWith("digraph") &&
    !line.trim().startsWith("rankdir") &&
    !line.trim().startsWith("node") &&
    !line.trim().startsWith("edge") &&
    !line.trim().startsWith("}")
  );

  relevantLines.forEach(line => {
    const trimmedLine = line.trim();
    const nodeDefinitionMatch = trimmedLine.match(/^(\w+)\s*\[/);
    const edgeStatementMatch = trimmedLine.match(/^(\w+)\s*->\s*(\w+)/);

    if (nodeDefinitionMatch) {
      const nodeName = nodeDefinitionMatch[1];
      assignColorToNode(nodeName); // Ensure color is assigned
      currentElements.push(trimmedLine);
    } else if (edgeStatementMatch) {
      const sourceNode = edgeStatementMatch[1];
      const targetNode = edgeStatementMatch[2];
      assignColorToNode(sourceNode); // Ensure colors for edge nodes
      assignColorToNode(targetNode);

      // Add edge with color from source node
      const sourceNodeData = nodesWithColors[sourceNode];
      if (sourceNodeData) {
        currentElements.push(`${sourceNode} -> ${targetNode} [color="${sourceNodeData.edgeColor}"];`);
      } else {
        currentElements.push(trimmedLine); // Fallback if no color found
      }
    }

    // Construct the DOT string for the current step
    let dotStep = `digraph G {${globalAttributes}`;

    // Add elements (nodes and edges) as they are encountered
    currentElements.forEach(element => {
      const nodeMatch = element.match(/^(\w+)\s*\[/);
      if (nodeMatch) {
        // Explicit node definition case (you already handled this correctly)
        const nodeName = nodeMatch[1];
        const nodeData = nodesWithColors[nodeName];
        const otherAttributes = nodeData.originalAttributes
          .split(',')
          .map(attr => attr.trim())
          .filter(attr => !attr.startsWith('fillcolor=') && !attr.startsWith('color=') && !attr.startsWith('style=') && attr !== '')
          .join(', ');

        const newAttributes = `style=filled, fillcolor="${nodeData.fill}", color="${nodeData.border}"${otherAttributes ? `, ${otherAttributes}` : ''}`;
        dotStep += `  ${nodeName} [${newAttributes}];\n`;

      } else if (element.includes("->")) {
        // Edge definition case
        dotStep += `  ${element.replace(/;+$/, '')};\n`;

        // Ensure both edge nodes are also defined with their palette colors
        const edgeMatch = element.match(/^(\w+)\s*->\s*(\w+)/);
        if (edgeMatch) {
          [edgeMatch[1], edgeMatch[2]].forEach(nodeName => {
            const nodeData = nodesWithColors[nodeName];
            if (nodeData) {
              const otherAttributes = nodeData.originalAttributes
                .split(',')
                .map(attr => attr.trim())
                .filter(attr => !attr.startsWith('fillcolor=') && !attr.startsWith('color=') && !attr.startsWith('style=') && attr !== '')
                .join(', ');
              const newAttributes = `style=filled, fillcolor="${nodeData.fill}", color="${nodeData.border}"${otherAttributes ? `, ${otherAttributes}` : ''}`;
              dotStep += `  ${nodeName} [${newAttributes}];\n`;
            }
          });
        }
      } else {
        // Handle other elements that are not explicit node definitions or edges
        dotStep += `  ${element.replace(/;+$/, '')};\n`;
      }
    });
    dotStep += "}";
    steps.push(dotStep);
  });

  // Ensure the final step is always present and complete
  let finalDot = `digraph G {${globalAttributes}`;

  // Add elements (nodes and edges) as they are encountered
  currentElements.forEach(element => {
    const nodeMatch = element.match(/^(\w+)\s*\[/);
    if (nodeMatch) {
      // Explicit node definition case
      const nodeName = nodeMatch[1];
      const nodeData = nodesWithColors[nodeName];
      const otherAttributes = nodeData.originalAttributes
        .split(',')
        .map(attr => attr.trim())
        .filter(attr => !attr.startsWith('fillcolor=') && !attr.startsWith('color=') && !attr.startsWith('style=') && attr !== '')
        .join(', ');

      const newAttributes = `style=filled, fillcolor="${nodeData.fill}", color="${nodeData.border}"${otherAttributes ? `, ${otherAttributes}` : ''}`;
      finalDot += `  ${nodeName} [${newAttributes}];\n`;

    } else if (element.includes("->")) {
      // Edge definition case
      const edgeMatch = element.match(/^(\w+)\s*->\s*(\w+)/);
      if (edgeMatch) {
        const sourceNode = edgeMatch[1];
        const targetNode = edgeMatch[2];
        const sourceNodeData = nodesWithColors[sourceNode];
        if (sourceNodeData) {
          finalDot += `  ${sourceNode} -> ${targetNode} [color="${sourceNodeData.edgeColor}"];\n`;
        } else {
          finalDot += `  ${element.replace(/;+$/, '')};\n`;
        }
      } else {
        finalDot += `  ${element.replace(/;+$/, '')};\n`;
      }

      // Ensure both edge nodes are also defined with their palette colors
      if (edgeMatch) {
        [edgeMatch[1], edgeMatch[2]].forEach(nodeName => {
          const nodeData = nodesWithColors[nodeName];
          if (nodeData) {
            const otherAttributes = nodeData.originalAttributes
              .split(',')
              .map(attr => attr.trim())
              .filter(attr => !attr.startsWith('fillcolor=') && !attr.startsWith('color=') && !attr.startsWith('style=') && attr !== '')
              .join(', ');
            const newAttributes = `style=filled, fillcolor="${nodeData.fill}", color="${nodeData.border}"${otherAttributes ? `, ${otherAttributes}` : ''}`;
            finalDot += `  ${nodeName} [${newAttributes}];\n`;
          }
        });
      }
    } else {
      // Handle other elements that are not explicit node definitions or edges
      finalDot += `  ${element.replace(/;+$/, '')};\n`;
    }
  });
  finalDot += "}";
  if (steps[steps.length - 1] !== finalDot) {
    steps.push(finalDot);
  }

  return { steps, nodesWithColors };
}

// -------------------- MAIN COMPONENT --------------------
export default function GraphvizViewer({ dotString, onSvgRendered }: GraphvizViewerProps) {
  const graphvizContainerRef = useRef<HTMLDivElement>(null);
  const graphvizInstanceRef = useRef<Graphviz<any, any, any, any> | null>(null);
  const [DOT_STEPS, setDOT_STEPS] = useState<string[]>([]);
  const [nodeColorsMap, setNodeColorsMap] = useState<{ [key: string]: { fill: string; border: string; edgeColor: string; originalAttributes: string } }>({});
  const [step, setStep] = useState(0); // State for current animation step
  const [done, setDone] = useState(false); // State to indicate animation completion
  const [isReady, setIsReady] = useState(false); // Track if graphviz is initialized

  // Generate DOT steps
  useEffect(() => {
    if (dotString) {
      const { steps: generatedSteps, nodesWithColors: generatedNodeColors } = generateDotSteps(dotString);
      setDOT_STEPS(generatedSteps);
      setNodeColorsMap(generatedNodeColors);
      setStep(0);
      setDone(false);
      // Reset isReady when dotString changes to force re-initialization
      setIsReady(false);
      graphvizInstanceRef.current = null;
    } else {
      setDOT_STEPS([]);
      setNodeColorsMap({});
      setStep(0);
      setDone(false);
    }
  }, [dotString]);

  // Initialize graphviz instance once
  useEffect(() => {
    // Wait for DOM to be ready
    const container = graphvizContainerRef.current;
    if (!container) {
      // Try again after a short delay
      const timeout = setTimeout(() => {
        const retryContainer = graphvizContainerRef.current;
        if (retryContainer && !graphvizInstanceRef.current) {
          initializeGraphviz(retryContainer);
        }
      }, 100);
      return () => clearTimeout(timeout);
    }

    if (!graphvizInstanceRef.current) {
      initializeGraphviz(container);
    }

    function initializeGraphviz(targetElement: HTMLDivElement) {
      import("d3").then((d3) => {
        try {
          graphvizInstanceRef.current = graphviz(targetElement, { useWorker: false, d3: d3 as any });
          setIsReady(true);
        } catch {
          setIsReady(false);
        }
      }).catch(() => {
        setIsReady(false);
      });
    }

    return () => {
      if (!graphvizContainerRef.current) {
        graphvizInstanceRef.current = null;
        setIsReady(false);
      }
    };
  }, [dotString]);

  // Graph rendering and step advancement
  useEffect(() => {
    if (DOT_STEPS.length === 0 || !graphvizInstanceRef.current || !isReady) {
      return;
    }

    const graphvizInstance = graphvizInstanceRef.current;
    const currentDotString = DOT_STEPS[step];

    // Dynamically import d3 for transition
    import("d3").then((d3) => {
      try {
        graphvizInstance
          .transition(d3.transition().ease(easeLinear).duration(2000) as any)
          .renderDot(currentDotString)
          .on("end", () => {
            if (step === DOT_STEPS.length - 1 && onSvgRendered) {
              const svgElement = graphvizContainerRef.current?.querySelector("svg");
              if (svgElement) {
                onSvgRendered(svgElement.outerHTML);
              }
            }

            const graphWrapper = document.querySelector(".graph-wrapper");
            if (graphWrapper) {
              graphWrapper.scrollLeft = graphWrapper.scrollWidth;
            }

            if (step < DOT_STEPS.length - 1) {
              // Advance ONLY after transition ends
              setStep((prev) => prev + 1);
            } else {
              setDone(true);
            }
          });
      } catch {
        // Silently handle render errors
      }
    });
  }, [step, DOT_STEPS, isReady]);

  // Optional: Kill all transitions on unmount
  useEffect(() => {
    return () => {
      if (graphvizContainerRef.current) {
        import("d3").then((d3) => {
          d3.select(graphvizContainerRef.current).selectAll("*").interrupt();
        });
      }
    };
  }, []);

  // Render the main container div with the ref
  return (
    <div ref={graphvizContainerRef} className="graph-wrapper overflow-auto w-full max-w-full" style={{ maxHeight: "80vh", padding: "10px" }}>
      <style jsx>{`
        .graph-wrapper {
          animation: fadeInScale 1s ease-out forwards;
        }
        @keyframes fadeInScale {
          from { opacity: 0; transform: scale(0.85); }
          to { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  );
}
