import dbus


class DbusInteractions:
    "Handles UWSM interactions via DBus"

    # global dbus objects for reuse
    dbus_objects = {"system": {}, "session": {}}

    def __init__(self, dbus_level: str):
        "Takes dbus_level as 'system' or 'session'"
        if dbus_level in ["system", "session"]:
            self.dbus_level = dbus_level
            if "bus" not in self.dbus_objects[self.dbus_level]:
                if dbus_level == "system":
                    self.dbus_objects[self.dbus_level]["bus"] = dbus.SystemBus()
                else:
                    self.dbus_objects[self.dbus_level]["bus"] = dbus.SessionBus()
        else:
            raise ValueError(
                f"dbus_level can be 'system' or 'session', got '{dbus_level}'"
            )

    def __str__(self):
        "Prints currently held global dbus_objects for debug purposes"
        return f"DbusInteraction, instance: {self.dbus_level}, global objects:\n{str(self.dbus_objects)}"

    # Internal functions (adding objects)

    def add_systemd(self):
        "Adds /org/freedesktop/systemd1 object"
        if "systemd" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["systemd"] = self.dbus_objects[
                self.dbus_level
            ]["bus"].get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1")

    def add_systemd_manager_interface(self):
        "Adds org.freedesktop.systemd1.Manager method interface"
        self.add_systemd()
        if "systemd_manager_interface" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["systemd_manager_interface"] = dbus.Interface(
                self.dbus_objects[self.dbus_level]["systemd"],
                "org.freedesktop.systemd1.Manager",
            )

    def add_systemd_properties_interface(self):
        "Adds org.freedesktop.systemd1.Manager properties interface"
        self.add_systemd()
        if "systemd_properties_interface" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["systemd_properties_interface"] = dbus.Interface(
                self.dbus_objects[self.dbus_level]["systemd"],
                "org.freedesktop.DBus.Properties",
            )

    def add_systemd_unit_properties(self, unit_id):
        "Adds unit properties interface of unit_id into nested unit_properties dict"
        self.add_systemd_manager_interface()
        unit_path = self.dbus_objects[self.dbus_level]["bus"].get_object(
            "org.freedesktop.systemd1",
            self.dbus_objects[self.dbus_level]["systemd_manager_interface"].GetUnit(unit_id),
        )
        if "unit_properties" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["unit_properties"] = {}
        if unit_id not in self.dbus_objects[self.dbus_level]["unit_properties"]:
            self.dbus_objects[self.dbus_level]["unit_properties"][unit_id] = (
                dbus.Interface(unit_path, "org.freedesktop.DBus.Properties")
            )

    def add_dbus(self):
        "Adds /org/freedesktop/DBus object"
        if "dbus" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["dbus"] = self.dbus_objects[
                self.dbus_level
            ]["bus"].get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")

    def add_dbus_interface(self):
        "Adds org.freedesktop.DBus interface"
        self.add_dbus()
        if "dbus_interface" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["dbus_interface"] = dbus.Interface(
                self.dbus_objects[self.dbus_level]["dbus"], "org.freedesktop.DBus"
            )

    def add_notifications(self):
        "Adds org.freedesktop.Notifications object"
        if "notifications" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["notifications"] = self.dbus_objects[
                self.dbus_level
            ]["bus"].get_object(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
            )

    def add_notifications_interface(self):
        "Adds org.freedesktop.Notifications interface"
        self.add_notifications()
        if "notifications_interface" not in self.dbus_objects[self.dbus_level]:
            self.dbus_objects[self.dbus_level]["notifications_interface"] = (
                dbus.Interface(
                    self.dbus_objects[self.dbus_level]["notifications"],
                    "org.freedesktop.Notifications",
                )
            )

    # External functions (doing stuff via objects)

    def get_unit_property(self, unit_id, unit_property):
        "Returns value of unit property"
        self.add_systemd_unit_properties(unit_id)
        return self.dbus_objects[self.dbus_level]["unit_properties"][unit_id].Get(
            "org.freedesktop.systemd1.Unit", unit_property
        )

    def reload_systemd(self):
        "Reloads systemd manager, returns job"
        self.add_systemd_manager_interface()
        return self.dbus_objects[self.dbus_level]["systemd_manager_interface"].Reload()

    def list_systemd_jobs(self):
        "Lists systemd jobs"
        self.add_systemd_manager_interface()
        return self.dbus_objects[self.dbus_level]["systemd_manager_interface"].ListJobs()

    def set_dbus_vars(self, vars_dict: dict):
        "Takes dict of ENV vars, puts them to dbus activation environment"
        self.add_dbus_interface()
        self.dbus_objects[self.dbus_level][
            "dbus_interface"
        ].UpdateActivationEnvironment(vars_dict)

    def set_systemd_vars(self, vars_dict: dict):
        "Takes dict of ENV vars, puts them to systemd activation environment"
        self.add_systemd_manager_interface()
        assignments = [f"{var}={value}" for var, value in vars_dict.items()]
        self.dbus_objects[self.dbus_level]["systemd_manager_interface"].SetEnvironment(
            assignments
        )

    def unset_systemd_vars(self, vars_list: list):
        "Takes list of ENV var names, unsets them from systemd activation environment"
        self.add_systemd_manager_interface()
        self.dbus_objects[self.dbus_level]["systemd_manager_interface"].UnsetEnvironment(
            vars_list
        )

    def get_systemd_vars(self):
        "Returns dict of ENV vars from systemd activation environment"
        self.add_systemd_properties_interface()
        assignments = self.dbus_objects[self.dbus_level]["systemd_properties_interface"].Get(
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
        self.add_systemd_manager_interface()
        return self.dbus_objects[self.dbus_level][
            "systemd_manager_interface"
        ].ListUnitsByPatterns(states, patterns)

    def stop_unit(self, unit: str, job_mode: str = "fail"):
        self.add_systemd_manager_interface()
        return self.dbus_objects[self.dbus_level]["systemd_manager_interface"].StopUnit(
            unit, job_mode
        )

    def notify(
        self,
        summary: str,
        body: str,
        app_name: str = "UWSM",
        replaces_id: int = 0,
        app_icon: str = "desktop",
        actions: list = None,
        hints: dict = None,
        expire_timeout: int = -1,
        # custom helpers
        urgency: int = 1,
    ):
        "Sends notification via Dbus"
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
        hints.update({"urgency": dbus.Byte(urgency)})
        self.add_notifications_interface()
        self.dbus_objects[self.dbus_level]["notifications_interface"].Notify(
            app_name,
            replaces_id,
            app_icon,
            summary,
            body,
            actions,
            hints,
            expire_timeout,
        )
