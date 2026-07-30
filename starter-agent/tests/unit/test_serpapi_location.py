import httpx

from starter_agent.tools.adapters.serpapi_location import (
    SerpApiLocationResolver,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://serpapi.com/locations.json")
            raise httpx.HTTPStatusError(
                "location lookup failed",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


async def test_location_resolver_uses_provider_canonical_name_without_local_map():
    client = FakeClient(
        FakeResponse(
            [
                {
                    "canonical_name": "Shenzhen,Guangdong Province,China",
                    "name": "Shenzhen",
                    "country_code": "cn",
                    "target_type": "City",
                    "reach": 100,
                }
            ]
        )
    )
    resolver = SerpApiLocationResolver(client=client)

    result = await resolver.resolve("Shenzhen")

    assert result.status == "resolved"
    assert result.requested == "Shenzhen"
    assert result.canonical_name == "Shenzhen,Guangdong Province,China"
    assert result.city_alias == "Shenzhen"
    assert result.country_code == "cn"
    assert client.calls == [
        {
            "url": "https://serpapi.com/locations.json",
            "params": {"q": "Shenzhen", "limit": 5},
            "timeout": 8.0,
        }
    ]


async def test_location_resolver_reports_not_found_for_unsupported_text():
    resolver = SerpApiLocationResolver(client=FakeClient(FakeResponse([])))

    result = await resolver.resolve("深圳")

    assert result.status == "not_found"
    assert result.canonical_name is None


async def test_location_resolver_degrades_when_provider_is_unavailable():
    request = httpx.Request("GET", "https://serpapi.com/locations.json")
    resolver = SerpApiLocationResolver(
        client=FakeClient(httpx.ReadTimeout("timeout", request=request))
    )

    result = await resolver.resolve("Any Region")

    assert result.status == "unavailable"
    assert result.canonical_name is None
    assert result.city_alias is None
    assert result.country_code is None


async def test_location_resolver_reads_non_china_alias_without_city_map():
    resolver = SerpApiLocationResolver(
        client=FakeClient(
            FakeResponse(
                [{
                    "canonical_name": "Munich,Bavaria,Germany",
                    "name": "Munich",
                    "country_code": "de",
                }]
            )
        )
    )

    result = await resolver.resolve("München")

    assert result.city_alias == "Munich"
    assert result.country_code == "de"
