import dbus
from uwsm.misc import print_debug


class DbusInteractions:
    "Handles UWSM interactions via DBus"

    # mapping of logical service keys to (bus_name, object_path)
    _SERVICES = {
        "systemd": ("org.freedesktop.systemd1", "/org/freedesktop/systemd1"),
        "dbus": ("org.freedesktop.DBus", "/org/freedesktop/DBus"),
        "login": ("org.freedesktop.login1", "/org/freedesktop/login1"),
        "notifications": (
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
        ),
    }

    # mapping of service key -> { iface_key: iface_name, ... }
    _INTERFACES = {
        "systemd": {
            "manager": "org.freedesktop.systemd1.Manager",
            "properties": "org.freedesktop.DBus.Properties",
        },
        "dbus": {
            "dbus": "org.freedesktop.DBus",
        },
        "login": {
            "manager": "org.freedesktop.login1.Manager",
            "properties": "org.freedesktop.DBus.Properties",
        },
        "notifications": {
            "notify": "org.freedesktop.Notifications",
        },
    }

    def __init__(self, dbus_level: str):
        "Takes dbus_level as 'system' or 'session'"
        if dbus_level in ["system", "session"]:
            print_debug("initiate dbus interaction", dbus_level)
            self.dbus_level = dbus_level
            self._level = dbus_level
            self._bus = None
            self.dbus_objects = {}
            self.dbus_objects["bus"] = self._get_bus()
            self._proxies = {}
            self._interfaces = {}
        else:
            raise ValueError(
                f"dbus_level can be 'system' or 'session', got '{dbus_level}'"
            )

    def __str__(self):
        "Prints currently held dbus_objects for debug purposes"
        return f"DbusInteractions, instance level: {self.dbus_level}, instance objects:\n{str(self.dbus_objects)}"

    def _get_bus(self):
        """Lazily return and cache the system or session bus."""
        if self._bus is None:
            self._bus = (
                dbus.SystemBus() if self._level == "system" else dbus.SessionBus()
            )
        return self._bus

    def _get_proxy(self, service_key: str):
        """Retrieve and cache a DBus object proxy for the given service."""
        if service_key not in self._proxies:
            bus_name, path = self._SERVICES[service_key]
            self._proxies[service_key] = self._get_bus().get_object(bus_name, path)
        return self._proxies[service_key]

    def _get_interface(self, service_key: str, iface_key: str):
        """Retrieve and cache a DBus Interface for the given service and interface."""
        cache_key = f"{service_key}_{iface_key}"
        if cache_key not in self._interfaces:
            proxy = self._get_proxy(service_key)
            iface_name = self._INTERFACES[service_key][iface_key]
            self._interfaces[cache_key] = dbus.Interface(proxy, iface_name)
        return self._interfaces[cache_key]

    def _get_unit_properties_iface(self, unit_id: str):
        """Retrieve and cache a DBus.Properties interface for the given systemd unit."""
        cache_key = f"unit_props_{unit_id}"
        if cache_key not in self._interfaces:
            # manager interface for systemd
            manager: dbus.Interface = self._get_interface("systemd", "manager")
            # get the unit object path
            unit_path = manager.GetUnit(unit_id)
            # fetch the raw object proxy
            unit_obj = self._get_bus().get_object("org.freedesktop.systemd1", unit_path)
            # wrap it in the standard Properties interface
            self._interfaces[cache_key] = dbus.Interface(
                unit_obj, "org.freedesktop.DBus.Properties"
            )
        return self._interfaces[cache_key]

    def get_properties(
        self, service_key: str, iface_key: str, iface_service: str, keys: list[str]
    ):
        """Retrieve and return the given properties from the specified DBus interface."""
        iface: dbus.Interface = self._get_interface(service_key, iface_key)
        props = {}
        for key in keys:
            props[key] = iface.Get(iface_service, key)
        return props

    def get_systemd_properties(self, keys):
        "Takes list of keys, returns dict of requested properties of systemd daemon"
        return self.get_properties(
            "systemd", "properties", "org.freedesktop.systemd1.Manager", keys
        )

    def get_login_properties(self, keys):
        "Takes list of keys, returns dict of requested properties of login daemon"
        return self.get_properties(
            "login", "properties", "org.freedesktop.login1.Manager", keys
        )

    # External functions (doing stuff via objects)

    def get_unit_property(self, unit_id, unit_property):
        "Returns value of unit property"
        iface = self._get_unit_properties_iface(unit_id)
        return iface.Get("org.freedesktop.systemd1.Unit", unit_property)

    def reload_systemd(self):
        "Reloads systemd manager, returns job"
        return self._get_interface("systemd", "manager").Reload()

    def list_systemd_jobs(self):
        "Lists systemd jobs"
        return self._get_interface("systemd", "manager").ListJobs()

    def set_dbus_vars(self, vars_dict: dict):
        "Takes dict of ENV vars, puts them to dbus activation environment"
        self._get_interface("dbus", "dbus").UpdateActivationEnvironment(vars_dict)

    def set_systemd_vars(self, vars_dict: dict):
        "Takes dict of ENV vars, puts them to systemd activation environment"
        assignments = [f"{var}={value}" for var, value in vars_dict.items()]
        self._get_interface("systemd", "manager").SetEnvironment(assignments)

    def unset_systemd_vars(self, vars_list: list):
        "Takes list of ENV var names, unsets them from systemd activation environment"
        self._get_interface("systemd", "manager").UnsetEnvironment(vars_list)

    def get_systemd_vars(self):
        "Returns dict of ENV vars from systemd activation environment"
        assignments = self._get_interface("systemd", "properties").Get(
            "org.freedesktop.systemd1.Manager", "Environment"
        )
        # Environment is returned as array of assignment strings
        # Seems to be safe to use .splitlines().
        env = {}
        for assignment in assignments:
            var, value = str(assignment).split("=", maxsplit=1)
            env.update({var: value})
        return env

    def list_units_by_patterns(self, states: list, patterns: list):
        "Takes a list of unit states and a list of unit patterns, returns list of dbus structs"
        return self._get_interface("systemd", "manager").ListUnitsByPatterns(
            states, patterns
        )

    def stop_unit(self, unit: str, job_mode: str = "fail"):
        return self._get_interface("systemd", "manager").StopUnit(unit, job_mode)

    def list_login_sessions(self):
        "Lists login sessions"
        return self._get_interface("login", "manager").ListSessions()

    def list_login_sessions_ex(self):
        "Lists login sessions"
        return self._get_interface("login", "manager").ListSessionsEx()

    def notify(
        self,
        summary: str,
        body: str,
        app_name: str = "UWSM",
        replaces_id: int = 0,
        app_icon: str = "desktop",
        actions: list | None = None,
        hints: dict | None = None,
        expire_timeout: int = -1,
        # custom helpers
        urgency: int = 1,
    ):
        "Sends notification via Dbus"
        iface = self._get_interface("notifications", "notify")
        if actions is None:
            # actions = dbus.Array([], signature='as')
            actions = []
        # else:
        #    actions = dbus.Array(actions, signature='as')
        if hints is None:
            hints = {}
        if not 0 <= urgency <= 2:
            raise ValueError(f"Urgency range is 0-2, got {urgency}")
        # plain integer does not work
        hints["urgency"] = dbus.Byte(urgency)
        iface.Notify(
            app_name,
            replaces_id,
            app_icon,
            summary,
            body,
            actions,
            hints,
            expire_timeout,
        )
