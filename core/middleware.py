from .access import active_tenant_memberships, is_super_admin


class ActiveTenantMiddleware:
    """Attach the current tenant membership to authenticated vendor requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.tenant_membership = None

        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False) and not is_super_admin(user):
            memberships = active_tenant_memberships(user)
            if memberships:
                request.tenant_membership = memberships[0]
                request.tenant = memberships[0].tenant

        return self.get_response(request)
