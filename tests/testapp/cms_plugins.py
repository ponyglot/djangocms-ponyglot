from cms.plugin_base import CMSPluginBase
from cms.plugin_pool import plugin_pool

from .models import Link, Teaser


@plugin_pool.register_plugin
class TeaserPlugin(CMSPluginBase):
    model = Teaser
    name = "Teaser"
    render_template = "teaser.html"
    allow_children = True


@plugin_pool.register_plugin
class DeclaredLinkPlugin(CMSPluginBase):
    model = Link
    name = "Declared link"
    render_template = "teaser.html"
    text_enabled = True
