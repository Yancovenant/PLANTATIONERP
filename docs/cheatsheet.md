# Part of Inphms, see License file for full copyright and licensing details.

## PLATFORM Operating System OS
import os
os.name = ['nt', 'posix']

nt = windows 10/11
posix = linux/macos/freebsd

## HTTP Status Code
- 302 Temporary Redirect
- 303 See Other


## PYTHON Typing
- `@`:
    - decorator that wrappped another function and return with new function or class.
    - order is bottom-up.
    - Examples:
        ```python
            @A
            @B
            def f(): ...
            # IS Equivalent to:
            f = A(B(f))
        ```
- `*`:
    - unpacking operator especially for [] list or () tuple.
    - Examples:
        ```python
            a, b, *c = [1, 2, 3, 4, 5]
            print(a) # 1
            print(b) # 2
            print(c) # [3, 4, 5]
        ```
- `**`:
    - unpacking operator especially for {} dictionary.
    - Examples:
        ```python
            a, b, **c = {'a': 1, 'b': 2, 'c': 3}
            print(a) # 1
            print(b) # 2
            print(c) # {'c': 3}
        ```
- `decorator or @decorator`:
    - **Preserves** the original function's metadata. (name, docstring, etc).
    - Make the wrapper function looks like the original function.
    - By default, wrapping replaces the function’s metadata (`__name__`, `__doc__`, etc.).
    - Examples:
    - **Without `wraps`:**
        ```python
            from decorator import decorator
            
            @decorator
            def _double(name):
                return f"Hello {name} {name}"

            def doubles(name):
                """This is Multi level decorator"""
                return _double(name)

            print(doubles("Ian"))
            print(doubles.__name__)   # doubles
            print(doubles.__doc__)    # This is Multi level decorator
        ```
        ---
    - **With `wraps`:**
        ```python
            from functools import wraps

            def my_decorator(func):
                @wraps(func)  # Preserves metadata
                def wrapper(*args, **kwargs):
                    print("Before call")
                    result = func(*args, **kwargs)
                    print("After call")
                    return result
                return wrapper
            
            @my_decorator
            def greet(name):
                """Say hello to someone."""
                return f"Hello {name}"
            
            @my_decorator
            def _double(name):
                return f"Hello {name} {name}"

            def doubles(name):
                """This is Multi level decorator"""
                return _double(name)

            print(greet("Ian"))
            print(greet.__name__)   # greet
            print(greet.__doc__)    # Say hello to someone.
            print(doubles("Ian"))
            print(doubles.__name__)   # doubles
            print(doubles.__doc__)    # This is Multi level decorator
        ```
    - done

## PYTHON MRO (Method Resolution Order)
- in order :
    - __new__
        - ```python
            class A:
                def __new__(cls):
                    print("new called")
                    return super().__new__(cls)
            class B(A): ## <- __new__ called # output : new called
                pass
          ```
    - __init__
    - __call__
    - __getattr__
    - __setattr__
    - __delattr__
    - __getattribute__
    - __setattribute__

- metaclass:
    - is used to control the creation of class process. e.g __new__



