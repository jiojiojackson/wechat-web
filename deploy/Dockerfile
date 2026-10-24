FROM ghcr.io/nickrunning/wechat-selkies@sha256:aefb35f900b3c821ae38830e88053d19a860e5be6960e25e2999de28d8e35d15

USER root
ENV DEBIAN_FRONTEND=noninteractive
COPY prune-image.py /opt/wechat-hardening/prune-image.py
RUN apt-get update \
 && apt-get -y upgrade \
 && python3 /opt/wechat-hardening/prune-image.py \
 && apt-get install -y --no-install-recommends apache2-utils \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
RUN /lsiopy/bin/pip install --no-cache-dir --upgrade 'PyJWT[crypto]==2.15.1' aiohttp==3.14.5 pillow==13.0.0 cryptography==50.0.2 pyOpenSSL==26.4.0 bcrypt==5.0.0 \
 && /lsiopy/bin/pip check

COPY access_guard.py /usr/local/lib/wechat-access-guard.py
COPY mobile-ime.js /opt/wechat-hardening/mobile-ime.js
COPY mobile-tools.js mobile-tools.css /opt/wechat-hardening/
COPY mobile-viewport.js /opt/wechat-hardening/
COPY safari-codecs.js /opt/wechat-hardening/
COPY install-mobile-ime.py /opt/wechat-hardening/install-mobile-ime.py
COPY nginx.conf /opt/wechat-hardening/nginx.conf
COPY init-hardening.sh /custom-cont-init.d/99-wechat-hardening
COPY menu.xml /defaults/menu.xml
COPY start-wechat.sh /scripts/start.sh
COPY entrypoint.sh /usr/local/bin/wechat-entrypoint
RUN mkdir -p /etc/s6-overlay/s6-rc.d/svc-wechat-access/dependencies.d \
 && printf '%s\n' longrun > /etc/s6-overlay/s6-rc.d/svc-wechat-access/type \
 && printf '%s\n' '#!/usr/bin/with-contenv bash' 'exec s6-setuidgid nobody /lsiopy/bin/python3 /usr/local/lib/wechat-access-guard.py' > /etc/s6-overlay/s6-rc.d/svc-wechat-access/run \
 && touch /etc/s6-overlay/s6-rc.d/svc-wechat-access/dependencies.d/init-services \
          /etc/s6-overlay/s6-rc.d/user/contents.d/svc-wechat-access \
 && chmod 0755 /etc/s6-overlay/s6-rc.d/svc-wechat-access/run /custom-cont-init.d/99-wechat-hardening /scripts/start.sh /usr/local/bin/wechat-entrypoint \
 && chmod 0644 /usr/local/lib/wechat-access-guard.py /opt/wechat-hardening/nginx.conf /defaults/menu.xml \
 && cp -a /defaults /opt/wechat-hardening/defaults \
 && find /usr /bin /sbin /opt -xdev -type f -perm /6000 -exec chmod a-s '{}' +

ENTRYPOINT ["/usr/local/bin/wechat-entrypoint"]

LABEL local.security.base.digest="sha256:aefb35f900b3c821ae38830e88053d19a860e5be6960e25e2999de28d8e35d15" \
      local.security.purpose="WeChat only; Access JWT, identity-bound persistent sessions, restricted origin and disabled command execution"
