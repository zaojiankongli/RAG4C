"""Bounded DNS resolution abstractions for enterprise identity control-plane checks."""

from __future__ import annotations

from typing import Protocol


class IdentityDnsError(RuntimeError):
    """Base DNS control-plane failure."""


class IdentityDnsResolverUnavailable(IdentityDnsError):
    """The optional resolver dependency or upstream DNS is unavailable."""


class IdentityTxtResolver(Protocol):
    def resolve_txt(self, name: str) -> tuple[str, ...]: ...

    def resolve_host_addresses(self, host: str) -> tuple[str, ...]: ...


class DnspythonIdentityResolver:
    """Lazy, bounded dnspython adapter; it never performs DNS writes."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 1.5,
        lifetime_seconds: float = 3.0,
        max_records: int = 32,
    ) -> None:
        if not (0.1 <= timeout_seconds <= 5.0):
            raise ValueError("DNS timeout must be between 0.1 and 5 seconds")
        if not (timeout_seconds <= lifetime_seconds <= 10.0):
            raise ValueError("DNS lifetime must be bounded and >= timeout")
        if not (1 <= max_records <= 64):
            raise ValueError("DNS record limit must be between 1 and 64")
        self.timeout_seconds = timeout_seconds
        self.lifetime_seconds = lifetime_seconds
        self.max_records = max_records

    def _resolver(self):
        try:
            import dns.resolver
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise IdentityDnsResolverUnavailable(
                "dnspython is not installed; install the identity extra"
            ) from exc
        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = self.timeout_seconds
        resolver.lifetime = self.lifetime_seconds
        return resolver

    @staticmethod
    def _dns_exceptions():
        try:
            import dns.exception
            import dns.resolver
        except ImportError as exc:  # pragma: no cover - optional extra
            raise IdentityDnsResolverUnavailable(
                "dnspython is not installed; install the identity extra"
            ) from exc
        return dns.exception, dns.resolver

    def resolve_txt(self, name: str) -> tuple[str, ...]:
        resolver = self._resolver()
        dns_exception, dns_resolver = self._dns_exceptions()
        try:
            answer = resolver.resolve(
                str(name),
                "TXT",
                lifetime=self.lifetime_seconds,
                search=False,
            )
        except (dns_resolver.NXDOMAIN, dns_resolver.NoAnswer):
            return ()
        except (dns_exception.Timeout, dns_resolver.NoNameservers) as exc:
            raise IdentityDnsResolverUnavailable("bounded TXT lookup failed") from exc
        values: list[str] = []
        for item in answer:
            strings = getattr(item, "strings", None)
            if strings is not None:
                value = b"".join(strings).decode("utf-8", errors="strict")
            else:
                value = str(item.to_text()).strip('"')
            if value and len(value) <= 512:
                values.append(value)
            if len(values) >= self.max_records:
                break
        return tuple(values)

    def resolve_host_addresses(self, host: str) -> tuple[str, ...]:
        resolver = self._resolver()
        dns_exception, dns_resolver = self._dns_exceptions()
        values: list[str] = []
        for record_type in ("A", "AAAA"):
            try:
                answer = resolver.resolve(
                    str(host),
                    record_type,
                    lifetime=self.lifetime_seconds,
                    search=False,
                )
            except (dns_resolver.NXDOMAIN, dns_resolver.NoAnswer):
                continue
            except (dns_exception.Timeout, dns_resolver.NoNameservers) as exc:
                raise IdentityDnsResolverUnavailable("bounded host address lookup failed") from exc
            for item in answer:
                value = str(getattr(item, "address", item.to_text()))
                if value:
                    values.append(value)
                if len(values) >= self.max_records:
                    return tuple(values)
        return tuple(values)


__all__ = [
    "DnspythonIdentityResolver",
    "IdentityDnsError",
    "IdentityDnsResolverUnavailable",
    "IdentityTxtResolver",
]
