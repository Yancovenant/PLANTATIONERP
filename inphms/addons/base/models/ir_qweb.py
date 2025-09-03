# Part of Inphms, see License file for full copyright and licensing details.
"""
================
IrQWeb / ir.qweb
================

Preamble
========

Technical documentation of the python operation of the rendering QWeb engine.

Templating
==========

QWeb is the primary templating engine used by Inphms. It is an XML templating
engine and used mostly to generate XML, HTML fragments and pages.

Template directives are specified as XML attributes prefixed with ``t-``,
for instance ``t-if`` for :ref:`reference/qweb/conditionals`, with elements
and other attributes being rendered directly.

To avoid element rendering, a placeholder element ``<t>`` is also available,
which executes its directive but doesn't generate any output in and of
itself.

To create new XML template, please see :doc:`QWeb Templates documentation
<https://www.inphms.com/documentation/master/developer/reference/frontend/qweb.html>`

Rendering process
=================

In **input** you have an XML template giving the corresponding input etree.
Each etree input nodes are used to generate a python function. This fonction is
called and will give the XML **output**.
The ``_compile`` method is responsible to generate the function from the
etree, that function is a python generator that yield one output line at a
time. This generator is consumed by ``_render``. The generated function is orm
cached.

For performance, the **compile time** (when input, XML template or template
id, is compiled into a function) is less important than the **rendering time**
(when the function is called with the different values). The generation of the
function is only done once (for a set of options, language, branding ...)
because it is cached orm

The output is in ``MarkupSafe`` format. ``MarkupSafe`` escapes characters so
text is safe to use in HTML and XML. Characters that have special meanings
are replaced so that they display as the actual characters. This mitigates
injection attacks, meaning untrusted user input can safely be displayed on a
page.

At **compile time**, each dynamic attribute ``t-*`` will be compiled into
specific python code. (For example ``<t t-out="5 + 5"/>`` will insert the
template "10" inside the output)

At **compile time**, each directive removes the dynamic attribute it uses from
the input node attributes. At the end of the compilation each input node, no
dynamic attributes must remain.

How the code works
==================

In the graphic below you can see theresume of the call of the methods performed
in the IrQweb class.

.. code-block:: rst

    Inphms
     ┗━► _render (returns MarkupSafe)
        ┗━► _compile (returns function)                                        ◄━━━━━━━━━━┓
           ┗━► _compile_node (returns code string array)                       ◄━━━━━━━━┓ ┃
              ┃  (skip the current node if found t-qweb-skip)                           ┃ ┃
              ┃  (add technical directives: t-tag-open, t-tag-close, t-inner-content)   ┃ ┃
              ┃                                                                         ┃ ┃
              ┣━► _directives_eval_order (defined directive order)                      ┃ ┃
              ┣━► _compile_directives (loop)    Consume all remaining directives ◄━━━┓  ┃ ┃
              ┃  ┃                              (e.g.: to change the indentation)    ┃  ┃ ┃
              ┃  ┣━► _compile_directive                                              ┃  ┃ ┃
              ┃  ┃    ┗━► t-nocache       ━━► _compile_directive_nocache            ━┫  ┃ ┃
              ┃  ┃    ┗━► t-cache         ━━► _compile_directive_cache              ━┫  ┃ ┃
              ┃  ┃    ┗━► t-groups        ━━► _compile_directive_groups             ━┫  ┃ ┃
              ┃  ┃    ┗━► t-foreach       ━━► _compile_directive_foreach            ━┫  ┃ ┃
              ┃  ┃    ┗━► t-if            ━━► _compile_directive_if                 ━┛  ┃ ┃
              ┃  ┃    ┗━► t-inner-content ━━► _compile_directive_inner_content ◄━━━━━┓ ━┛ ┃
              ┃  ┃    ┗━► t-options       ━━► _compile_directive_options             ┃    ┃
              ┃  ┃    ┗━► t-set           ━━► _compile_directive_set           ◄━━┓  ┃    ┃
              ┃  ┃    ┗━► t-call          ━━► _compile_directive_call            ━┛ ━┫ ━━━┛
              ┃  ┃    ┗━► t-att           ━━► _compile_directive_att                 ┃
              ┃  ┃    ┗━► t-tag-open      ━━► _compile_directive_open          ◄━━┓  ┃
              ┃  ┃    ┗━► t-tag-close     ━━► _compile_directive_close         ◄━━┫  ┃
              ┃  ┃    ┗━► t-out           ━━► _compile_directive_out             ━┛ ━┫ ◄━━┓
              ┃  ┃    ┗━► t-field         ━━► _compile_directive_field               ┃   ━┫
              ┃  ┃    ┗━► t-esc           ━━► _compile_directive_esc                 ┃   ━┛
              ┃  ┃    ┗━► t-*             ━━► ...                                    ┃
              ┃  ┃                                                                   ┃
              ┗━━┻━► _compile_static_node                                           ━┛


The QWeb ``_render`` uses the function generated by the ``_compile`` method.
Each XML node will go through the ``_compile_node`` method. If the
node does not have dynamic directives or attributes (``_is_static_node``).
A ``static`` is a node without ``t-*`` attributes, does not require dynamic
rendering for its attributes.
If it's a ``static`` node, the ``_compile_static_node`` method is called,
otherwise it is the ``_compile_directives`` method after having prepared the
order for calling the directives using the ``_directives_eval_order`` method.
In the defined order, for each directive the method ``_compile_directive`` is
called which itself dispatches to the methods corresponding to the directives
``_compile_directive_[name of the directive]`` (for example: ``t-if`` =>
``_compile_directive_if``). After all ordered directives, the directives
attributes still present on the element are compiled.

The ``_post_processing_att`` method is used for the generation of rendering
attributes. If the attributes come from static XML template nodes then the
method is called only once when generating the render function. Otherwise the
method is called during each rendering.

Each expression is compiled by the method ``_compile_expr`` into a python
expression whose values are namespaced.

Directives
----------

``t-debug``
~~~~~~~~~~~
**Values**: `''` (empty string), ``pdb``, ``ipdb``, ``pudb``, ``wdb``

Triggers a debugger breakpoint at that location. With an empty value, calls the
``breakpoint`` builtin invoking whichever breakpoint hook has been set up,
otherwise triggers a breakpoint uses the corresponding debugger.

When dev mode is enabled this allows python developers to have access to the
state of variables being rendered. The code generated by the QWeb engine is
not accessible, only the variables (values, self) can be analyzed or the
methods that called the QWeb rendering.

.. warning:: using a non-empty string is deprecated since 17.0, configure your
             preferred debugger via ``PYTHONBREAKPOINT`` or
             ``sys.setbreakpointhook``.

``t-if``
~~~~~~~~
**Values**: python expression


Add an python ``if`` condition to the code string array, and call
``_compile_directives`` to level and add the code string array corresponding
to the other directives and content.

The structure of the dom is checked to possibly find a ``t-else`` or
``t-elif``. If these directives exist then the compilation is performed and
the nodes are marked not to be rendered twice.

At **rendering time** the other directives code and content will used only if
the expression is evaluated as truely.

The ``t-else``, ``t-elif`` and ``t-if`` are not compiled at the same time like
defined in ``_directives_eval_order`` method.
```
<t t-set="check" t-value="1"/>
<section t-if="False">10</section>
<span t-elif="check == 1" t-foreach="range(3)" t-as="check" t-esc="check"/>

<section t-if="False">10</section>
<div t-else="" t-if="check == 1" t-foreach="range(3)" t-as="check" t-esc="check"/>

Result:

<span>0</span>
<span>1</span>
<span>2</span>

<div>1</div>
```

``t-else``
~~~~~~~~~~
**Values**: nothing

Only validate the **input**, the compilation if inside the ``t-if`` directive.

``t-elif``
~~~~~~~~~~
**Values**: python expression

Only validate the **input**, the compilation if inside the ``t-if`` directive.

``t-groups`` (``groups`` is an alias)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
**Values**: name of the allowed inphms user group, or preceded by ``!`` for
prohibited groups

The generated code uses ``has_group`` Inphms method from ``res.users`` model.

``t-foreach``
~~~~~~~~~~~~~
**Values**: an expression returning the collection to iterate on

This directive is used with ``t-as`` directive to defined the key name. The
directive will be converted into a ``for`` loop. In this loop, different values
are added to the dict (``values`` in the generated method) in addition to the
key defined by ``t-name``, these are (``*_value``, ``*_index``, ``*_size``,
``*_first``, ``*_last``).

``t-as``
~~~~~~~~
**Values**: key name

The compilation method only validates if ``t-as`` and ``t-foreach`` are on the
same node.

``t-options`` and ``t-options-*``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
**Values**: python expression

It's use on the same node of another directive, it's used to configure the
other directive. Used on the same ``input node`` of the directives ``t-call``,
``t-field`` or ``t-out``.

Create a ``values['__qweb_options__']`` dict from the optional ``t-options``
expression and add each key-value ``t-options-key="expression value"`` to this
dict. (for example: ``t-options="{'widget': 'float'}"`` is equal to
``t-options-widget="'float'"``)

``t-att``, ``t-att-*`` and ``t-attf-*``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
**Values**: python expression (or format string expression for ``t-attf-``)

Compile the attributes to create ``values['__qweb_attrs__']`` dictionary code
in the compiled function. Use the ``t-att`` expression and add each key-value
``t-att-key="expression value"`` to this dict. (for example:
``t-att="{'class': f'float_{1}'}"`` is equal to ``t-att-class="f'float_{1}'"``
and is equal to ``t-attf-class="float_{{1}}")

The attributes come from new namespaces, static elements (not preceded
by ``t-``) and dynamic attributes ``t-att``, attributes prefixed by ``t-att-``
(python expression) or ``t-attf`` (format string expression).

``t-call``
~~~~~~~~~~
**Values**: format string expression for template name

Serves the called template in place of the current ``t-call`` node.

Here are the different steps performed by the generated python code:

#. copy the ``values`` dictionary;
#. render the content (``_compile_directive_inner_content``) of the tag in a
   separate method called with the previous copied values. This values can be
   updated via t-set. The visible content of the rendering of the sub-content
   is added as a magical value ``0`` (can be rendered with ``t-out="0"``);
#. copy the ``compile_context`` dictionary;
#. compile the directive ``t-options`` and update the ``compile_context``
    are, in added to the calling template and the ``nsmap`` values;
#. get the compiled function from the ``_compile`` method;
#. use the compiled function to serves the called template.

``t-lang``
~~~~~~~~~~
**Values**: python expression

Used to serve the called template (``t-call``) in another language. Used
together with ``t-call``.

This directive will be evaluate like ``t-options-lang``. Allows you to change
the language in which the called template is rendered. It's in the ``t-call``
directive that the language of the context of the ``ir.qweb`` recordset on
which the ``_compile`` function is called is updated.

``t-call-assets``
~~~~~~~~~~~~~~~~~
**Values**: format string for template name

The generated code call the ``_get_asset_nodes`` method to get the list of
(tagName, attrs and content). From each tuple a tag is created into the
rendering.

``t-out``
~~~~~~~~~
**Values**: python expression

Output the given value or if falsy, display the content as default value.
(for example: ``<t t-out="given_value">Default content</t>``)

The generated code add the value into the ``MarkupSafe`` rendering.
If a widget is defined (``t-options-widget``), the generated code call the
``_get_widget`` method to have the formatted field value and attributes. It's
the ``ir.qweb.field.*`` models that format the value.

``t-field``
~~~~~~~~~~~
**Values**: String representing the path to the field. (for example:
``t-field="record.name"``)

Output the field value or if falsy, display the content as default value.
(for example: ``<span t-field="record.name">Default content</span>``)

Use ``t-out`` compile method but the generated code call ``_get_field``
instead of ``_get_widget``. It's the ``ir.qweb.field.*`` models that format
the value. The rendering model is chosen according to the type of field. The
rendering model can be modified via the ``t-options-widget``.

``t-esc``
~~~~~~~~~
Deprecated, please use ``t-out``

``t-raw``
~~~~~~~~~
Deprecated, please use ``t-out``

``t-set``
~~~~~~~~~
**Values**: key name

The generated code update the key ``values`` dictionary equal to the value
defined by ``t-value`` expression, ``t-valuef`` format string expression or
to the ``MarkupSafe`` rendering come from the content of the node.

``t-value``
~~~~~~~~~~~
**Values**: python expression

The compilation method only validates if ``t-value`` and ``t-set`` are on the
same node.

``t-valuef``
~~~~~~~~~~~~
**Values**: format string expression

The compilation method only validates if ``t-valuef`` and ``t-set`` are on the
same node.

Technical directives
--------------------

Directive added automatically by IrQweb in order to go through the compilation
methods.

``t-tag-open``
~~~~~~~~~~~~~~
Used to generate the opening HTML/XML tags.

``t-tag-close``
~~~~~~~~~~~~~~
Used to generate the closing HTML/XML tags.

``t-inner-content``
~~~~~~~~~~~~~~~~~~~
Used to add the content of the node (text, tail and children nodes).
If namespaces are declared on the current element then a copy of the options
is made.

``t-consumed-options``
~~~~~~~~~~~~~~~~~~~~~~
Raise an exception if the ``t-options`` is not consumed.

``t-qweb-skip``
~~~~~~~~~~~~~~~~~~~~~~
Ignore rendering and directives for the curent **input** node.

``t-else-valid``
~~~~~~~~~~~~~~~~~~~~~~
Mark a node with ``t-else`` or ``t-elif`` having a valid **input** dom
structure.

"""

import base64
import contextlib
import fnmatch
import io
import logging
import math
import re
import textwrap
import time
import token
import tokenize
import traceback
import warnings
import werkzeug

import psycopg2.errors
from markupsafe import Markup, escape
from collections.abc import Sized, Mapping
from itertools import count, chain
from lxml import etree
from dateutil.relativedelta import relativedelta
from psycopg2.extensions import TransactionRollbackError

from inphms import api, models, tools
from inphms.modules import registry
# from inphms.tools import config, safe_eval, pycompat
from inphms.tools import config, safe_eval
# from inphms.tools.constants import SUPPORTED_DEBUGGER, EXTERNAL_ASSET
from inphms.tools.safe_eval import assert_valid_codeobj, _BUILTINS, to_opcodes, _EXPR_OPCODES, _BLACKLIST
from inphms.tools.json import scriptsafe
from inphms.tools.lru import LRU
# from inphms.tools.misc import str2bool
from inphms.tools.image import image_data_uri, FILETYPE_BASE64_MAGICWORD
from inphms.http import request
from inphms.tools.profiler import QwebTracker
# from inphms.exceptions import UserError, AccessDenied, AccessError, MissingError, ValidationError

# from inphms.addons.base.models.assetsbundle import AssetsBundle
# from inphms.tools.constants import SCRIPT_EXTENSIONS, STYLE_EXTENSIONS, TEMPLATE_EXTENSIONS

_logger = logging.getLogger(__name__)


# QWeb token usefull for generate expression used in `_compile_expr_tokens` method
token.QWEB = token.NT_OFFSET - 1
token.tok_name[token.QWEB] = 'QWEB'


# security safe eval opcodes for generated expression validation, used in `_compile_expr`
_SAFE_QWEB_OPCODES = _EXPR_OPCODES.union(to_opcodes([
    'MAKE_FUNCTION', 'CALL_FUNCTION', 'CALL_FUNCTION_KW', 'CALL_FUNCTION_EX',
    'CALL_METHOD', 'LOAD_METHOD',

    'GET_ITER', 'FOR_ITER', 'YIELD_VALUE',
    'JUMP_FORWARD', 'JUMP_ABSOLUTE', 'JUMP_BACKWARD',
    'JUMP_IF_FALSE_OR_POP', 'JUMP_IF_TRUE_OR_POP', 'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE',

    'LOAD_NAME', 'LOAD_ATTR',
    'LOAD_FAST', 'STORE_FAST', 'UNPACK_SEQUENCE',
    'STORE_SUBSCR',
    'LOAD_GLOBAL',
    'EXTENDED_ARG',
    # Following opcodes were added in 3.11 https://docs.python.org/3/whatsnew/3.11.html#new-opcodes
    'RESUME',
    'CALL',
    'PRECALL',
    'PUSH_NULL',
    'KW_NAMES',
    'FORMAT_VALUE', 'BUILD_STRING',
    'RETURN_GENERATOR',
    'SWAP',
    'POP_JUMP_FORWARD_IF_FALSE', 'POP_JUMP_FORWARD_IF_TRUE',
    'POP_JUMP_BACKWARD_IF_FALSE', 'POP_JUMP_BACKWARD_IF_TRUE',
    'POP_JUMP_FORWARD_IF_NONE', 'POP_JUMP_FORWARD_IF_NOT_NONE',
    'POP_JUMP_BACKWARD_IF_NONE', 'POP_JUMP_BACKWARD_IF_NOT_NONE',
    # 3.12 https://docs.python.org/3/whatsnew/3.12.html#new-opcodes
    'END_FOR',
    'LOAD_FAST_AND_CLEAR',
    'POP_JUMP_IF_NOT_NONE', 'POP_JUMP_IF_NONE',
    'RERAISE',
    'CALL_INTRINSIC_1',
    'STORE_SLICE',
    # 3.13
    'CALL_KW', 'LOAD_FAST_LOAD_FAST',
    'STORE_FAST_STORE_FAST', 'STORE_FAST_LOAD_FAST',
    'CONVERT_VALUE', 'FORMAT_SIMPLE', 'FORMAT_WITH_SPEC',
    'SET_FUNCTION_ATTRIBUTE',
])) - _BLACKLIST


# eval to compile generated string python code into binary code, used in `_compile`
unsafe_eval = eval


VOID_ELEMENTS = frozenset([
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'keygen',
    'link', 'menuitem', 'meta', 'param', 'source', 'track', 'wbr'])
# Terms allowed in addition to AVAILABLE_OBJECTS when compiling python expressions
ALLOWED_KEYWORD = frozenset(['False', 'None', 'True', 'and', 'as', 'elif', 'else', 'for', 'if', 'in', 'is', 'not', 'or'] + list(_BUILTINS))
# regexpr for string formatting and extract ( ruby-style )|( jinja-style  ) used in `_compile_format`
FORMAT_REGEX = re.compile(r'(?:#\{(.+?)\})|(?:\{\{(.+?)\}\})')
RSTRIP_REGEXP = re.compile(r'\n[ \t]*$')
LSTRIP_REGEXP = re.compile(r'^[ \t]*\n')
FIRST_RSTRIP_REGEXP = re.compile(r'^(\n[ \t]*)+(\n[ \t])')
VARNAME_REGEXP = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
TO_VARNAME_REGEXP = re.compile(r'[^A-Za-z0-9_]+')
# Attribute name used outside the context of the QWeb.
SPECIAL_DIRECTIVES = {'t-translation', 't-ignore', 't-title'}
# Name of the variable to insert the content in t-call in the template.
# The slot will be replaced by the `t-call` tag content of the caller.
T_CALL_SLOT = '0'


def keep_query(*keep_params, **additional_params):
    """
    Generate a query string keeping the current request querystring's parameters specified
    in ``keep_params`` and also adds the parameters specified in ``additional_params``.

    Multiple values query string params will be merged into a single one with comma seperated
    values.

    The ``keep_params`` arguments can use wildcards too, eg:

        keep_query('search', 'shop_*', page=4)
    """
    if not keep_params and not additional_params:
        keep_params = ('*',)
    params = additional_params.copy()
    qs_keys = list(request.httprequest.args) if request else []
    for keep_param in keep_params:
        for param in fnmatch.filter(qs_keys, keep_param):
            if param not in additional_params and param in qs_keys:
                params[param] = request.httprequest.args.getlist(param)
    return werkzeug.urls.url_encode(params)


####################################
###             QWeb             ###
####################################

class IrQWeb(models.AbstractModel):
    """ Base QWeb rendering engine
    * to customize ``t-field`` rendering, subclass ``ir.qweb.field`` and
      create new models called :samp:`ir.qweb.field.{widget}`
    Beware that if you need extensions or alterations which could be
    incompatible with other subsystems, you should create a local object
    inheriting from ``ir.qweb`` and customize that.
    """

    _name = 'ir.qweb'
    _description = 'Qweb'

    @QwebTracker.wrap_render
    @api.model
    def _render(self, template, values=None, **options):
        """ render(template, values, **options)

        Render the template specified by the given name.

        :param template: etree, xml_id, template name (see _get_template)
            * Call the method ``load`` is not an etree.
        :param dict values: template values to be used for rendering
        :param options: used to compile the template
            Options will be add into the IrQweb.env.context for the rendering.
            * ``lang`` (str) used language to render the template
            * ``inherit_branding`` (bool) add the tag node branding
            * ``inherit_branding_auto`` (bool) add the branding on fields
            * ``minimal_qcontext``(bool) To use the minimum context and options
                from ``_prepare_environment``

        :returns: bytes marked as markup-safe (decode to :class:`markupsafe.Markup`
                  instead of `str`)
        :rtype: MarkupSafe
        """
        values = values.copy() if values else {}
        if T_CALL_SLOT in values:
            raise ValueError(f'values[{T_CALL_SLOT}] should be unset when call the _render method and only set into the template.')

        irQweb = self.with_context(**options)._prepare_environment(values)

        safe_eval.check_values(values)
        
        template_functions, def_name = irQweb._compile(template)
        render_template = template_functions[def_name]
        rendering = render_template(irQweb, values)
        result = ''.join(rendering)

        return Markup(result)
    
    # assume cache will be invalidated by third party on write to ir.ui.view
    def _get_template_cache_keys(self):
        """ Return the list of context keys to use for caching ``_compile``. """
        return ['lang', 'inherit_branding', 'inherit_branding_auto', 'edit_translations', 'profile']
    
    @tools.conditional(
        'xml' not in tools.config['dev_mode'],
        tools.ormcache('template', 'tuple(self.env.context.get(k) for k in self._get_template_cache_keys())', cache='templates'),
    )
    def _get_view_id(self, template):
        print("trying to get view id", template, self.__class__.__name__, self.env)
        try:
            return self.env['ir.ui.view'].sudo().with_context(load_all_views=True)._get_view_id(template)
        except Exception:
            return None
    
    @QwebTracker.wrap_compile
    def _compile(self, template):
        if isinstance(template, etree._Element):
            self = self.with_context(is_t_cache_disabled=True)
            ref = None
        else:
            ref = self._get_view_id(template)

        # define the base key cache for code in cache and t-cache feature
        base_key_cache = None
        if ref:
            base_key_cache = self._get_cache_key(tuple([ref] + [self.env.context.get(k) for k in self._get_template_cache_keys()]))
        self = self.with_context(__qweb_base_key_cache=base_key_cache)

    # values for running time

    def _get_converted_image_data_uri(self, base64_source):
        if self.env.context.get('webp_as_jpg'):
            mimetype = FILETYPE_BASE64_MAGICWORD.get(base64_source[:1], 'png')
            if 'webp' in mimetype:
                # Use converted image so that is recognized by wkhtmltopdf.
                bin_source = base64.b64decode(base64_source)
                Attachment = self.env['ir.attachment']
                checksum = Attachment._compute_checksum(bin_source)
                origins = Attachment.sudo().search([
                    ['id', '!=', False],  # No implicit condition on res_field.
                    ['checksum', '=', checksum],
                ])
                if origins:
                    converted_domain = [
                        ['id', '!=', False],  # No implicit condition on res_field.
                        ['res_model', '=', 'ir.attachment'],
                        ['res_id', 'in', origins.ids],
                        ['mimetype', '=', 'image/jpeg'],
                    ]
                    converted = Attachment.sudo().search(converted_domain, limit=1)
                    if converted:
                        base64_source = converted.datas
        return image_data_uri(base64_source)
    
    def _prepare_environment(self, values):
        """ Prepare the values and context that will sent to the
        compiled and evaluated function.

        :param values: template values to be used for rendering

        :returns self (with new context)
        """
        debug = request and request.session.debug or ''
        values.update(
            true=True,
            false=False,
        )
        if not self.env.context.get('minimal_qcontext'):
            values.setdefault('debug', debug)
            values.setdefault('user_id', self.env.user.with_env(self.env))
            values.setdefault('res_company', self.env.company.sudo())
            values.update(
                request=request,  # might be unbound if we're not in an httprequest context
                test_mode_enabled=bool(config['test_enable'] or config['test_file']),
                json=scriptsafe,
                quote_plus=werkzeug.urls.url_quote_plus,
                time=safe_eval.time,
                datetime=safe_eval.datetime,
                relativedelta=relativedelta,
                image_data_uri=self._get_converted_image_data_uri,
                # specific 'math' functions to ease rounding in templates and lessen controller marshmalling
                floor=math.floor,
                ceil=math.ceil,
                env=self.env,
                lang=self.env.context.get('lang'),
                keep_query=keep_query,
            )

        context = {'dev_mode': 'qweb' in tools.config['dev_mode']}
        if 'xml' in tools.config['dev_mode']:
            context['is_t_cache_disabled'] = True
        elif 'disable-t-cache' in debug:
            context['is_t_cache_disabled'] = True
        return self.with_context(**context)

def render(template_name, values, load, **options):
    """ Rendering of a qweb template without database and outside the registry.
    (Widget, field, or asset rendering is not implemented.)
    :param (string|int) template_name: template identifier
    :param dict values: template values to be used for rendering
    :param def load: function like `load(template_name)` which returns an etree
        from the given template name (from initial rendering or template
        `t-call`).
    :param options: used to compile the template
    :returns: bytes marked as markup-safe (decode to :class:`markupsafe.Markup`
                instead of `str`)
    :rtype: MarkupSafe
    """
    class MockPool:
        db_name = None
        _Registry__caches = {cache_name: LRU(cache_size) for cache_name, cache_size in registry._REGISTRY_CACHES.items()}
        _Registry__caches_groups = {}
        for cache_name, cache in _Registry__caches.items():
            _Registry__caches_groups.setdefault(cache_name.split('.')[0], []).append(cache)

    class MockIrQWeb(IrQWeb):
        _register = False               # not visible in real registry

        pool = MockPool()

        def _load(self, ref):
            """
            Load the template referenced by ``ref``.

            :returns: The loaded template (as string or etree) and its
                identifier
            :rtype: Tuple[Union[etree, str], Optional[str, int]]
            """
            return self.env.context['load'](ref)

        def _prepare_environment(self, values):
            values['true'] = True
            values['false'] = False
            return self.with_context(is_t_cache_disabled=True, __qweb_loaded_values={})

        def _get_field(self, *args):
            raise NotImplementedError("Fields are not allowed in this rendering mode. Please use \"env['ir.qweb']._render\" method")

        def _get_widget(self, *args):
            raise NotImplementedError("Widgets are not allowed in this rendering mode. Please use \"env['ir.qweb']._render\" method")

        def _get_asset_nodes(self, *args):
            raise NotImplementedError("Assets are not allowed in this rendering mode. Please use \"env['ir.qweb']._render\" method")

    class MockEnv(dict):
        def __init__(self):
            super().__init__()
            self.context = {}

        def __call__(self, cr=None, user=None, context=None, su=None):
            """ Return an mocked environment based and update the sent context.
                Allow to use `ir_qweb.with_context` with sand boxed qweb.
            """
            print("mock env is called", context)
            env = MockEnv()
            env.context.update(self.context if context is None else context)
            return env

    renderer = MockIrQWeb(MockEnv(), tuple(), tuple())
    return renderer._render(template_name, values, load=load, minimal_qcontext=True, **options)