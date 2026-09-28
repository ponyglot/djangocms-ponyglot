from cms.plugin_base import CMSPluginBase
from cms.plugin_pool import plugin_pool

from .models import Teaser


@plugin_pool.register_plugin
class TeaserPlugin(CMSPluginBase):
    model = Teaser
    name = "Teaser"
    render_template = "teaser.html"
    allow_children = True
