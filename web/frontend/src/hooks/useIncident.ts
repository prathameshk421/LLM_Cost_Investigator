import { useEffect, useState } from "react";
import { getIncident, listIncidents } from "../api/client";
import type { IncidentSummary, ReplayIncident } from "../types/replay";

export function useIncidentList() {
  const [items, setItems] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listIncidents()
      .then((data) => {
        if (!cancelled) {
          setItems(data);
          setError(null);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { items, loading, error };
}

export function useIncident(id: string | null) {
  const [incident, setIncident] = useState<ReplayIncident | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) {
      setIncident(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getIncident(id)
      .then((data) => {
        if (!cancelled) setIncident(data);
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setIncident(null);
          setError(err.message);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return { incident, loading, error };
}
