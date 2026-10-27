import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";
import { ThemeToggle } from "@/components/ThemeToggle";
import { IdleScreen } from "@/screens/IdleScreen";
import { SubmittingScreen } from "@/screens/SubmittingScreen";
import { ResultScreen } from "@/screens/ResultScreen";
import { ErrorScreen } from "@/screens/ErrorScreen";
import { useClassifyMachine } from "@/lib/useClassifyMachine";
import { applyTheme, getInitialTheme, type Theme } from "@/lib/theme";

const queryClient = new QueryClient();

/**
 * The whole screen as an explicit four-state state machine (idle,
 * submitting, result, error) -- not URL-based routing. useClassifyMachine
 * owns all of the state transitions; this component is a pure switch
 * over `state.status`.
 */
function ClassifyApp() {
  const machine = useClassifyMachine();
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = getInitialTheme();
    applyTheme(initial);
    return initial;
  });

  const toggleTheme = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  };

  return (
    <div className="min-h-screen bg-bg font-sans text-text">
      <div className="flex justify-end px-4 pt-4 sm:px-6">
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
      </div>

      {machine.state.status === "idle" && (
        <IdleScreen
          image={machine.state.image}
          description={machine.state.description}
          onImageChange={machine.setImage}
          onDescriptionChange={machine.setDescription}
          onSubmit={machine.submit}
        />
      )}

      {machine.state.status === "submitting" && <SubmittingScreen />}

      {machine.state.status === "result" && (
        <ResultScreen
          image={machine.state.image}
          data={machine.state.data}
          onNewMeasurement={machine.reset}
        />
      )}

      {machine.state.status === "error" && (
        <ErrorScreen
          code={machine.state.code}
          onRetry={machine.retry}
          onEditDescription={machine.backToIdlePreservingInput}
          onBackToIdle={machine.backToIdlePreservingInput}
        />
      )}
    </div>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider>
        <ClassifyApp />
      </MantineProvider>
    </QueryClientProvider>
  );
}
