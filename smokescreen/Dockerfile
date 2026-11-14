# Stripe Smokescreen, built from the v0.1.0 release commit (the ghcr.io image is not publicly pullable).
FROM golang:1.26.10-alpine AS build
ARG SMOKESCREEN_COMMIT=609eb8931420453daf5893509be0b25b21bd9edb
RUN apk add --no-cache git \
 && git init /src && cd /src \
 && git fetch --depth 1 https://github.com/stripe/smokescreen "$SMOKESCREEN_COMMIT" \
 && git checkout FETCH_HEAD \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /smokescreen . \
 && mkdir /licenses \
 && cp LICENSE.txt /licenses/Smokescreen-LICENSE.txt \
 && cp vendor/modules.txt /licenses/modules.txt \
 && find vendor -type f \( -iname '*license*' -o -iname 'copying*' -o -iname '*notice*' \) \
      -exec sh -c 'for file do mkdir -p "/licenses/$(dirname "$file")"; cp "$file" "/licenses/$file"; done' sh {} +

FROM scratch
COPY --from=build /smokescreen /smokescreen
COPY --from=build /licenses /licenses
USER 65534:65534
EXPOSE 4750
ENTRYPOINT ["/smokescreen"]
