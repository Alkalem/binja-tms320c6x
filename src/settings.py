# Copyright 2026 Benedikt Waibel
# 
# This file is part of the binary ninja tms320c6x architecture plugin.
# 
# This plugin is free software: 
# you can redistribute it and/or modify it under the terms of the GNU General
# Public License as published by the Free Software Foundation, either version 3
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.

from binaryninja.settings import Settings

import json


def register_settings():
    plugin_settings = Settings()
    plugin_settings.register_group('tms320c6x', 'TMS320C6x')

    properties = {
        'title': 'FP header details',
        'type': 'boolean',
        'description': 'Show detailed properties of each FP header in disassembly.',
        'default': True,
        'ignore': ['SettingsProjectScope', 'SettingsResourceScope']
    }
    plugin_settings.register_setting('tms320c6x.showFPHeaderDetails', json.dumps(properties))
