export async function apiCall({
  baseUrl,
  path,
  method = "GET",
  query,
  body
}) {
  const url = new URL(path, baseUrl);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }

  const options = {
    method,
    headers: {}
  };

  if (body !== null && body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  const start = performance.now();
  try {
    const res = await fetch(url.toString(), options);
    const duration = Math.round(performance.now() - start);
    const contentType = res.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await res.json()
      : await res.text();

    return {
      ok: res.ok,
      status: res.status,
      duration,
      payload
    };
  } catch (error) {
    const duration = Math.round(performance.now() - start);
    return {
      ok: false,
      status: 0,
      duration,
      payload: { error: error?.message || "Network error" }
    };
  }
}
