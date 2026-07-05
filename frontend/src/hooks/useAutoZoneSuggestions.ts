import { Dispatch, SetStateAction } from "react";
import { PPESuggestion, ZoneSuggestion } from "@/types/zone";
import { DraftZone } from "./camera-panel-types";

/**
 * Handlers for sign-detection-driven zone/PPE suggestions. The suggestion
 * *state* itself lives in `useLiveStream` (that's where the websocket
 * events actually arrive) and is passed in here — this hook only owns the
 * accept/dismiss/enable behavior on top of it.
 */
export function useAutoZoneSuggestions({
  sourceKey,
  isStreaming,
  sendMessage,
  zoneSuggestions,
  setZoneSuggestions,
  ppeSuggestions,
  setPpeSuggestions,
  zonesForVideo,
  setZonesForVideo,
  persistZones,
  setZoneEnabled,
  setPpeEnabled,
  openModifyModeFor,
}: {
  sourceKey: string | undefined;
  isStreaming: boolean;
  sendMessage: (payload: object) => void;
  zoneSuggestions: Record<string, ZoneSuggestion>;
  setZoneSuggestions: Dispatch<SetStateAction<Record<string, ZoneSuggestion>>>;
  ppeSuggestions: Record<string, PPESuggestion>;
  setPpeSuggestions: Dispatch<SetStateAction<Record<string, PPESuggestion>>>;
  zonesForVideo: DraftZone[];
  setZonesForVideo: (zones: DraftZone[]) => void;
  persistZones: (zones: DraftZone[]) => Promise<void>;
  setZoneEnabled: (enabled: boolean) => void;
  setPpeEnabled: (enabled: boolean) => void;
  openModifyModeFor: (draft: DraftZone) => void;
}) {
  const handleDismissSuggestion = (suggestion: ZoneSuggestion) => {
    sendMessage({ event: "dismiss_suggestion", data: { suggestion_id: suggestion.suggestion_id } });
    setZoneSuggestions(({ [suggestion.suggestion_id]: _, ...rest }) => rest);
  };

  const handleEnablePPESuggestion = (suggestion: PPESuggestion) => {
    setPpeSuggestions(({ [suggestion.suggestion_id]: _, ...rest }) => rest);
    setPpeEnabled(true);
  };

  const handleDismissPPESuggestion = (suggestion: PPESuggestion) => {
    sendMessage({ event: "dismiss_ppe_suggestion", data: { suggestion_id: suggestion.suggestion_id } });
    setPpeSuggestions(({ [suggestion.suggestion_id]: _, ...rest }) => rest);
  };

  const handleAcceptSuggestion = async (suggestion: ZoneSuggestion, name: string) => {
    if (!sourceKey) return;
    setZoneSuggestions(({ [suggestion.suggestion_id]: _, ...rest }) => rest);
    const draft: DraftZone = {
      id: crypto.randomUUID(),
      name,
      type: suggestion.zone_type,
      dwellThresholdSeconds: suggestion.zone_type === "WALKWAY" ? 3 : 0.5,
      points: suggestion.normalized_coordinates,
    };
    const nextZones = [...zonesForVideo, draft];
    setZonesForVideo(nextZones); // optimistic — show the zone on the overlay right away
    // Enable zone monitoring immediately on accept: turning on zoneEnabled pushes
    // update_settings to the backend, and persistZones saves the new zone and
    // sends reload_zones so the running pipeline enforces it on the next frame
    // (the retroactive foot-history check then catches anyone already inside).
    setZoneEnabled(true);
    await persistZones(nextZones);
    if (!isStreaming) {
      // Outside streaming, also open modify mode so the user can fine-tune the
      // auto-placed polygon; re-saving updates the persisted zone.
      openModifyModeFor(draft);
    }
  };

  return {
    zoneSuggestions,
    ppeSuggestions,
    handleDismissSuggestion,
    handleEnablePPESuggestion,
    handleDismissPPESuggestion,
    handleAcceptSuggestion,
  };
}
